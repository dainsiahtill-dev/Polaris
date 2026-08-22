"""Workspace quality checks implementation extracted from ``OrchestrationStageExecutor``.

Holds the workspace-quality-checks method cluster using the impl-passing
pattern: each function takes ``executor`` (the original ``self``) as its first
parameter so it can reach back into the class for shared state and helper
methods. Behavior is preserved verbatim.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from polaris.cells.runtime.task_runtime.public import (
    BindRuntimeTaskToFactoryRunCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import (
    TaskRuntimeService,
    bind_runtime_task_to_factory_run,
)
from polaris.kernelone.llm.budget_policy import FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS

from .factory_run_models import _WORKSPACE_VALIDATION_TIMEOUT_SECONDS, FactoryRun
from .factory_workspace_quality_evidence import (
    _dedupe_workspace_repair_paths,
    compact_go_stack_overflow_diagnostic,
    leftover_rotate_allows_quality_extra_round,
    leftover_targets_should_force_owner_rotate,
    workspace_quality_latest_task_boundary_scope_filter,
    workspace_quality_repair_result_has_mutation,
    workspace_quality_unclaimed_failing_tu_targets,
    workspace_quality_unclaimed_residual_targets,
)

# Module-local constants (mirrors of the ones in ``factory_stage_executor`` so
# the impl is self-contained without importing the executor module).
_QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS = FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS
_QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS = 5.0
# Phase-gated compilers (rustc/tsc) unmask one diagnostic class per repair
# round (E0432 -> E0277 -> E0507, live L1-05), so the same-run loop needs
# headroom beyond the two-round stagnation breaker while staying bounded by
# the per-round deadline checks and the oscillation-aware stagnation counter.
# Live L2-15 remint-21: last round was ``progress`` (7 compile residuals
# back to 5 CLI abort) then the hard cap stopped the loop. C++ syntax ->
# cmake -> unittest unmask needs more than 5 same-run rounds.
_WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS = 8
# Non-progress classes can alternate (for example equal-count swap, no-op,
# stale edit) and thereby evade the per-class consecutive-stagnation breaker.
# Permit two such rounds so newly structured diagnostics still get one local
# repair opportunity, but require verified progress by the third round.
_WORKSPACE_QUALITY_REPAIR_NONPROGRESS_HARD_CAP = 3
_WORKSPACE_QUALITY_REPAIR_MIN_LLM_START_BUDGET_SECONDS = FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS
_WORKSPACE_QUALITY_REPAIR_LEASE_TTL_SECONDS = 300
_WORKSPACE_QUALITY_REPAIR_HEARTBEAT_INTERVAL_SECONDS = 30.0

_UNITTEST_FAILED_SUMMARY_RE = re.compile(r"\bFAILED\s*\((?P<counts>[^)]*)\)", re.IGNORECASE)
_UNITTEST_COUNT_RE = re.compile(r"\b(?P<kind>failures|errors)\s*=\s*(?P<count>\d+)\b", re.IGNORECASE)
_UNITTEST_RAN_COUNT_RE = re.compile(r"\bRan\s+(?P<count>\d+)\s+tests?\b", re.IGNORECASE)
_TEST_MODALITY_SHORTFALL_RE = re.compile(
    r"(?:\bRan\s+0\s+tests?\b|test_source_files\s*=\s*\d+\s*<|test_assertion_count\s*=\s*\d+\s*<)",
    re.IGNORECASE,
)
_PYTEST_COUNT_RE = re.compile(r"\b(?P<count>\d+)\s+(?P<kind>failed|error|errors)\b", re.IGNORECASE)
_DIAGNOSTIC_LINE_LABEL_RE = re.compile(r"\bline\s+\d+\b", re.IGNORECASE)
_DIAGNOSTIC_FILE_LOCATION_RE = re.compile(r"(?P<path>(?:[A-Za-z]:)?[^\s:\n]+\.[A-Za-z0-9_+-]+):\d+(?::\d+)?")
_NAMED_TEST_FAILURE_RES = (
    ("go", re.compile(r"(?m)^---\s+FAIL:\s+(?P<test>[^\s(]+)", re.IGNORECASE)),
    ("unittest", re.compile(r"(?m)^(?:FAIL|ERROR):\s+(?P<test>[^\s(]+)", re.IGNORECASE)),
    ("pytest", re.compile(r"(?m)^(?P<test>\S+::\S+)\s+(?:FAILED|ERROR)\b", re.IGNORECASE)),
)
_GO_COMPILER_DIAGNOSTIC_RE = re.compile(
    r"(?:^|\s)(?:[A-Za-z]:)?[^\s:\n]+\.go:\d+(?::\d+)?:[^\n]*(?:"
    r"cannot\s+convert|undefined|declared\s+and\s+not\s+used|imported\s+and\s+not\s+used|"
    r"has\s+no\s+field\s+or\s+method|invalid\s+operation|syntax\s+error|"
    r"not\s+enough\s+arguments|too\s+many\s+arguments"
    r")",
    re.IGNORECASE | re.MULTILINE,
)


def _is_workspace_quality_test_target(path: str) -> bool:
    """Return whether a verifier path is a test wrapper, across languages.

    Go reports package-local paths such as ``engine_test.go`` while Java and
    Python commonly encode the test role in the basename.  Treating only
    JavaScript ``*.test.*`` as tests made the quality loop claim a test task
    before the implementation owner and, for package-local Go paths, fail with
    ``workspace_quality_repair_canonical_owner_missing``.
    """

    normalized = str(path or "").strip().replace("\\", "/")
    candidate = Path(normalized)
    lowered_parts = {part.casefold() for part in candidate.parts[:-1]}
    lowered_name = candidate.name.casefold()
    stem = candidate.stem.casefold()
    return bool(
        lowered_parts.intersection({"test", "tests", "__tests__"})
        or lowered_name.startswith("test_")
        or lowered_name.endswith("_test.go")
        or lowered_name.endswith("_test.py")
        or ".test." in lowered_name
        or ".spec." in lowered_name
        or (candidate.suffix.casefold() in {".java", ".kt", ".kts"} and stem.endswith("test"))
    )


def _workspace_quality_deterministic_probe_signature(errors: Sequence[str]) -> tuple[str, ...]:
    """Stable identity for deciding whether to repeat a no-effect deterministic probe.

    The normal verifier signature intentionally preserves paths, symbols, and
    source locations for effect classification.  It is too precise for the
    no-commit cache: a later LLM edit can shift traceback line numbers without
    changing the failing diagnostic, causing the same deterministic schedule
    to reopen and fail another TaskRuntime attempt.  Mask only volatile source
    locations here; keep filenames and diagnostic text so a real failure-class
    change still earns one deterministic probe.
    """

    normalized: set[str] = set()
    for error in errors:
        text = " ".join(str(error or "").split()).casefold()
        if not text:
            continue
        text = _DIAGNOSTIC_LINE_LABEL_RE.sub("line #", text)
        text = _DIAGNOSTIC_FILE_LOCATION_RE.sub(lambda match: f"{match.group('path')}:#", text)
        normalized.add(text)
    return tuple(sorted(normalized))


def _workspace_quality_failing_test_identities(errors: Sequence[str]) -> set[str]:
    """Extract stable named-test identities from verifier diagnostics.

    Runner duration, package footer, cache state, and assertion values may
    legitimately vary between identical failing-test runs.  Those volatile
    strings must not masquerade as a new diagnostic set or duplicate a
    regression guard.  Keep this conservative: only explicit runner test-name
    anchors count; compiler/prose diagnostics continue using full signatures.
    """

    identities: set[str] = set()
    for error in errors:
        text = str(error or "")
        for framework, pattern in _NAMED_TEST_FAILURE_RES:
            identities.update(
                f"{framework}:{match.group('test').casefold()}"
                for match in pattern.finditer(text)
                if str(match.group("test") or "").strip()
            )
    return identities


def _workspace_quality_is_pure_named_test_surface(errors: Sequence[str]) -> bool:
    """Return true only when every diagnostic item names a failing test.

    This intentionally rejects mixed compiler/test surfaces.  A stable test
    identity must not hide removal of an independent compile barrier, which is
    real forward progress even while the test remains red.
    """

    normalized = [str(error or "").strip() for error in errors if str(error or "").strip()]
    return bool(normalized) and all(_workspace_quality_failing_test_identities([error]) for error in normalized)


def _workspace_quality_test_failure_counts(result: Mapping[str, Any]) -> tuple[int, int] | None:
    """Return ``(failures, errors)`` from a Python test verifier result.

    Diagnostic-set cardinality is not a safe monotonic-progress metric for
    test runners: one newly introduced exception can collapse many distinct
    assertion failures into one deduplicated traceback signature.  Live L3-21
    changed 4 failures into 1 failure + 21 errors, yet the smaller normalized
    signature was accepted as ``progress``.  Parse the runner's own terminal
    summary so that common-exception fanout remains visible to convergence.
    """

    command = tuple(str(part or "").strip().casefold() for part in result.get("command") or ())
    is_unittest = "unittest" in command
    is_pytest = any(Path(part).name.casefold() in {"pytest", "py.test"} for part in command)
    if not is_unittest and not is_pytest:
        return None
    output = "\n".join(
        str(result.get(key) or "")
        for key in ("diagnostic_excerpt", "stdout_tail", "stderr_tail")
        if str(result.get(key) or "").strip()
    )
    if is_unittest:
        summaries = list(_UNITTEST_FAILED_SUMMARY_RE.finditer(output))
        if not summaries:
            return (0, 0) if bool(result.get("passed")) else None
        counts = {"failures": 0, "errors": 0}
        for match in _UNITTEST_COUNT_RE.finditer(summaries[-1].group("counts")):
            counts[match.group("kind").casefold()] = int(match.group("count"))
        return counts["failures"], counts["errors"]
    counts = {"failed": 0, "errors": 0}
    for match in _PYTEST_COUNT_RE.finditer(output):
        kind = match.group("kind").casefold()
        counts["errors" if kind in {"error", "errors"} else "failed"] = int(match.group("count"))
    if counts["failed"] or counts["errors"] or bool(result.get("passed")):
        return counts["failed"], counts["errors"]
    return None


def _workspace_quality_unittest_run_count(result: Mapping[str, Any]) -> int | None:
    """Return unittest's authoritative discovered/executed test count."""

    command = tuple(str(part or "").strip().casefold() for part in result.get("command") or ())
    if "unittest" not in command:
        return None
    output = "\n".join(
        str(result.get(key) or "")
        for key in ("diagnostic_excerpt", "stdout_tail", "stderr_tail")
        if str(result.get(key) or "").strip()
    )
    matches = list(_UNITTEST_RAN_COUNT_RE.finditer(output))
    return int(matches[-1].group("count")) if matches else None


def workspace_quality_verifier_regressed(
    before_results: Sequence[Mapping[str, Any]],
    after_results: Sequence[Mapping[str, Any]],
) -> bool:
    """True when post-edit verifier evidence is strictly worse.

    This is intentionally narrow: a previously passing command becoming red,
    or a Python test runner reporting more failures/errors, is regression.
    Compiler phase changes remain governed by diagnostic signatures.
    """

    def command_key(result: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(str(part or "").strip() for part in result.get("command") or () if str(part or "").strip())

    before_by_command = {command_key(result): result for result in before_results if command_key(result)}
    after_by_command = {command_key(result): result for result in after_results if command_key(result)}
    for command, before in before_by_command.items():
        after = after_by_command.get(command)
        if after is None:
            continue
        if bool(before.get("passed")) and not bool(after.get("passed")):
            return True
        before_run_count = _workspace_quality_unittest_run_count(before)
        after_run_count = _workspace_quality_unittest_run_count(after)
        if (
            before_run_count is not None
            and after_run_count is not None
            and after_run_count < before_run_count
        ):
            return True
        before_counts = _workspace_quality_test_failure_counts(before)
        after_counts = _workspace_quality_test_failure_counts(after)
        if before_counts is None or after_counts is None:
            continue
        before_failures, before_errors = before_counts
        after_failures, after_errors = after_counts
        if after_errors > before_errors or after_failures + after_errors > before_failures + before_errors:
            return True
    return False


async def _run_workspace_quality_repair_heartbeat(
    authority: Any,
    *,
    stop: asyncio.Event,
    failures: list[dict[str, Any]],
    context_summary: str,
) -> None:
    """Keep a claimed repair task alive while planning/provider work runs.

    Ordinary Director execution owns a background TaskRuntime heartbeat.  The
    Factory quality-repair continuation used the same 300-second claim but did
    not start that heartbeat, so long deterministic/LLM repair work could only
    reach DEO after its lease had expired.  The physical write then failed
    closed with ``deo_execution_attempt_heartbeat_failed`` and settlement
    failed again with ``session_lease_expired``.

    ``authority_operation_in_progress`` is a transient overlap with DEO's own
    atomic heartbeat/settlement operation.  DEO remains authoritative and
    fail-closed; the keeper simply retries on the next interval.
    """

    while True:
        try:
            await asyncio.wait_for(
                stop.wait(),
                timeout=_WORKSPACE_QUALITY_REPAIR_HEARTBEAT_INTERVAL_SECONDS,
            )
            return
        except asyncio.TimeoutError:
            pass
        try:
            verdict = await asyncio.to_thread(
                authority.heartbeat,
                lease_ttl_seconds=_WORKSPACE_QUALITY_REPAIR_LEASE_TTL_SECONDS,
                lock_timeout_seconds=5.0,
                context_summary=context_summary,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            failures.append(
                {
                    "code": "heartbeat_exception",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            return
        if bool(getattr(verdict, "success", False)):
            continue
        code = str(getattr(verdict, "code", "") or "heartbeat_rejected")
        if code == "authority_operation_in_progress":
            continue
        failures.append({"code": code})
        return


async def _stop_workspace_quality_repair_heartbeat(
    heartbeat_task: asyncio.Task[None],
    stop: asyncio.Event,
) -> None:
    stop.set()
    await heartbeat_task


async def _settle_pending_workspace_quality_repair_attempt(
    executor,
    pending: Mapping[str, Any] | None,
    *,
    accepted: bool,
    reason: str,
) -> dict[str, Any] | None:
    """Settle one repair attempt only after its verifier-owned decision."""

    if not isinstance(pending, Mapping):
        return None
    task_row_id = str(pending.get("task_row_id") or "").strip()
    execution_attempt = pending.get("execution_attempt")
    task_id = str(pending.get("task_id") or "").strip()
    if not task_row_id or execution_attempt is None:
        return None
    heartbeat_task = pending.get("heartbeat_task")
    heartbeat_stop = pending.get("heartbeat_stop")
    heartbeat_failures = pending.get("heartbeat_failures")
    if isinstance(heartbeat_task, asyncio.Task) and isinstance(heartbeat_stop, asyncio.Event):
        await _stop_workspace_quality_repair_heartbeat(heartbeat_task, heartbeat_stop)
    failures = heartbeat_failures if isinstance(heartbeat_failures, list) else []
    if failures:
        accepted = False
        reason = f"workspace_quality_repair_lease_heartbeat_failed:{failures[0].get('code', 'unknown')}"
    settle_result = executor._settle_director_stage_materialization_attempt(
        task_row_id=task_row_id,
        execution_attempt=execution_attempt,
        stage_status="success" if accepted else "failed",
        summary=reason,
    )
    return {
        "task_id": task_id,
        "session_id": str(getattr(execution_attempt, "session_id", "") or ""),
        "settled": bool(settle_result.get("success")),
        "outcome": "completed" if accepted else "failed",
        "success_authority": "post_repair_verifier" if accepted else "repair_attempt_failure",
    }


def _is_deferred_declared_test_entrypoint_issue(
    issue: Any,
    *,
    declared_targets: set[str],
) -> bool:
    """Ignore only test paths that a later PM task is contracted to create.

    Workspace quality repair runs after Director materialization but before all
    downstream tasks necessarily settle.  A manifest task may therefore create
    ``"test": "node --test tests/"`` before the test-owner task creates
    ``tests/product.test.js``.  Treating that discovery path as a missing
    *entrypoint* sends repair planning down an unrelated deterministic rule and
    hides the real verifier failure.

    This is not a final-quality waiver: the real test command still runs and
    remains authoritative.  Unowned/mistyped paths remain errors.
    """

    if str(getattr(issue, "code", "") or "").strip() != "npm_script_missing_local_entrypoint":
        return False
    metadata = getattr(issue, "metadata", None)
    metadata = metadata if isinstance(metadata, Mapping) else {}
    script_name = str(metadata.get("script_name") or "").strip().lower()
    if script_name != "test" and not script_name.startswith("test:"):
        return False
    entrypoint = str(metadata.get("entrypoint") or "").strip().replace("\\", "/")
    while entrypoint.startswith("./"):
        entrypoint = entrypoint[2:]
    if not entrypoint:
        return False
    prefix = entrypoint.rstrip("/") + "/"
    return any(target == entrypoint or target.startswith(prefix) for target in declared_targets)


_RESIDUAL_RS_PATH_RE = re.compile(r"-->\s+(?P<path>[^:\s]+\.rs):\d+")


def _workspace_quality_residuals_miss_mutated_paths(
    residual_errors: Sequence[str],
    repair_results: Sequence[Mapping[str, Any]] | None,
    owner_paths: Sequence[str] | None = None,
) -> bool:
    """True when residual rustc paths on the claimed owner were not mutated.

    Live L2-14: crate rewrite mutated ``src/main.rs`` (TASK-2) while tests
    still failed. Treating any leftover path as "uncovered residual" launched
    a same-owner LLM that invented ``pirate_treasure_budgeter_models`` and
    regressed the deterministic rewrite. Other-owner residuals must wait for
    the next exact-owner claim.
    """

    residual_paths: set[str] = set()
    for error in residual_errors or ():
        for match in _RESIDUAL_RS_PATH_RE.finditer(str(error or "")):
            residual_paths.add(match.group("path").replace("\\", "/").lstrip("./"))
    owned = {str(path or "").replace("\\", "/").lstrip("./") for path in owner_paths or () if str(path or "").strip()}
    if owned:
        residual_paths &= owned
    if not residual_paths:
        return False
    mutated_paths: set[str] = set()
    for item in repair_results or ():
        payload = dict(item) if isinstance(item, Mapping) else {}
        if not workspace_quality_repair_result_has_mutation(payload):
            continue
        raw_result = payload.get("result")
        result = raw_result if isinstance(raw_result, Mapping) else {}
        file_name = str(result.get("file") or result.get("path") or "").replace("\\", "/").lstrip("./")
        if file_name:
            mutated_paths.add(file_name)
    return bool(residual_paths - mutated_paths)


def _workspace_quality_round_owner_paths(round_summary: Mapping[str, Any] | None) -> list[str]:
    """Prefer claimed-owner evidence over optional repair_target_files.

    Live L2-14: deterministic crate rewrite mutated ``src/main.rs`` but the
    summary omitted ``repair_target_files``. Empty owner_paths skipped the
    intersect, leftover ``tests/product.rs`` residuals launched a same-owner
    LLM against stale E0433 text.
    """

    payload = dict(round_summary) if isinstance(round_summary, Mapping) else {}
    evidence = payload.get("task_boundary_owner_evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    for key in ("owner_target_files", "in_scope_diagnostic_target_files"):
        raw = evidence.get(key)
        if isinstance(raw, list | tuple) and raw:
            return [str(item) for item in raw if str(item or "").strip()]
    raw = payload.get("repair_target_files")
    if isinstance(raw, list | tuple):
        return [str(item) for item in raw if str(item or "").strip()]
    return []


def _workspace_quality_claimed_owner_diagnostic_targets(
    round_summary: Mapping[str, Any] | None,
) -> list[str]:
    """Return verifier targets proven writable by this round's live claim.

    A verifier blob can name several task boundaries.  The deterministic pass
    already claims the owner of the current primary diagnostic and records the
    exact in-scope subset.  LLM fallback must consume that evidence instead of
    re-running broad path heuristics that prefer a later non-test traceback and
    silently move repair from TASK-3 to TASK-2/TASK-1.
    """

    payload = dict(round_summary) if isinstance(round_summary, Mapping) else {}
    evidence_raw = payload.get("task_boundary_owner_evidence")
    evidence = dict(evidence_raw) if isinstance(evidence_raw, Mapping) else {}
    if (
        str(evidence.get("source") or "") != "task_runtime_execution_attempt"
        or not bool(evidence.get("director_local_repair_allowed"))
        or not str(evidence.get("task_id") or "").strip()
    ):
        return []
    owner_targets = set(_dedupe_workspace_repair_paths(evidence.get("owner_target_files") or []))
    in_scope_targets = _dedupe_workspace_repair_paths(evidence.get("in_scope_diagnostic_target_files") or [])
    return [path for path in in_scope_targets if path in owner_targets]


_CRATE_REWRITE_HOLD_MARKERS = (
    "unlinked crate",
    "cannot find module or crate",
    "can't find crate",
    "cannot find crate",
    " in `crate`",
    "unresolved import `crate::",
)


def _workspace_quality_plannable_source_tools(plan_probe: Mapping[str, Any] | None) -> set[str]:
    payload = dict(plan_probe) if isinstance(plan_probe, Mapping) else {}
    return {
        str(item or "").strip()
        for item in (payload.get("plannable_source_tools") or ())
        if str(item or "").strip()
    }


def _workspace_quality_hold_llm_for_plannable_deterministic(
    plan_probe: Mapping[str, Any] | None,
    *,
    write_tool_evidence: bool,
    residual_errors: Sequence[str] | None = None,
) -> bool:
    """Hold Director LLM while an owner-scoped crate rewrite is still plannable.

    Live L2-14: crate rewrite committed ``treasure_budget::`` on ``src/main.rs``,
    then leftover ``tests/product.rs`` residuals plus ``write_tool_evidence``
    unblocked LLM. The model used stale E0433 text and reverted the rewrite.
    A successful deterministic write must not ungate LLM while the same
    crate rewrite is still the plan for the current diagnostics.
    """

    del write_tool_evidence
    tools = _workspace_quality_plannable_source_tools(plan_probe)
    hold_tools = {
        "deterministic_rust_crate_import_rewrite_repair",
        "deterministic_rust_lib_root_facade_repair",
        "deterministic_rust_line_suggestion_repair",
        "deterministic_rust_field_rename_suggestion_repair",
        "deterministic_rust_trait_import_repair",
    }
    if tools & hold_tools:
        return True
    blob = "\n".join(str(item or "") for item in (residual_errors or ()))
    lowered = blob.lower()
    return any(marker in blob or marker in lowered for marker in _CRATE_REWRITE_HOLD_MARKERS)


def _workspace_quality_repair_errors(executor, results: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for result in results:
        if bool(result.get("passed")):
            continue
        error_text = str(result.get("error") or "").strip()
        diagnostic_excerpt = str(result.get("diagnostic_excerpt") or "").strip()
        stream_output = "\n".join(
            str(result.get(key) or "").strip()
            for key in ("stdout_tail", "stderr_tail")
            if str(result.get(key) or "").strip()
        )
        # ``diagnostic_excerpt`` is already the bounded, marker-aware projection
        # of stdout+stderr. Feeding it together with both tails duplicates the
        # same failure block up to three times and multiplies repair coverage.
        # Prefer it as the sole diagnostic input; command/error provenance stays
        # in the durable workspace-validation command row.
        diagnostic_input = diagnostic_excerpt or stream_output or error_text
        if not diagnostic_input:
            continue
        diagnostic_input = compact_go_stack_overflow_diagnostic(diagnostic_input)
        command = result.get("command")
        command_text = " ".join(str(part) for part in command) if isinstance(command, list) else str(command or "")
        output = executor._trim_command_output(diagnostic_input)
        # The command row is durable verifier evidence, but its wrapper is
        # not itself a repair diagnostic.  Feeding the entire wrapper into
        # Director Runtime makes the actionable nested compiler/runtime
        # diagnostic compete with generic ``workspace_validation_failed``
        # rows.  Coverage then fails closed even when an executable repair
        # binding exists.  Project through the public Director diagnostic
        # normalizer and transport only actionable raw diagnostics.  Keep
        # the wrapper as a fail-closed fallback when no actionable signal
        # can be extracted; command/phase/stdout/stderr provenance remains
        # authoritative in ``workspace-validation.json.commands``.
        try:
            from polaris.cells.director.runtime.public import normalize_director_repair_diagnostics

            diagnostics = normalize_director_repair_diagnostics((output,))
        except (ImportError, RuntimeError, TypeError, ValueError):
            diagnostics = ()
        actionable = [
            diagnostic
            for diagnostic in diagnostics
            if str(diagnostic.code or "").strip() not in {"artifact_quality_error", "workspace_validation_failed"}
        ]
        if actionable:
            errors.extend(
                str(diagnostic.metadata.get("raw") or diagnostic.message or "").strip()
                for diagnostic in actionable
                if str(diagnostic.metadata.get("raw") or diagnostic.message or "").strip()
            )
        else:
            fallback_output = executor._trim_command_output("\n".join(part for part in (error_text, output) if part))
            errors.append(
                "Artifact quality scan failed: workspace validation command failed"
                f" ({command_text or 'unknown command'}): {fallback_output}"
            )

    try:
        from polaris.kernelone.quality import scan_workspace_artifact_quality_evidence

        evidence = scan_workspace_artifact_quality_evidence(str(executor.workspace))
        declared_targets = {
            str(path or "").strip().replace("\\", "/")
            for path in executor._workspace_quality_repair_target_files()
            if str(path or "").strip()
        }
        deferred_error_messages = {
            str((getattr(issue, "metadata", None) or {}).get("raw") or "").strip()
            for issue in evidence.issues
            if _is_deferred_declared_test_entrypoint_issue(
                issue,
                declared_targets=declared_targets,
            )
        }
        errors.extend(error for error in evidence.errors if str(error or "").strip() not in deferred_error_messages)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        errors.append(f"Artifact quality scan failed: workspace quality repair scan failed: {exc}")

    deduped: list[str] = []
    seen: set[str] = set()
    for error in errors:
        normalized = str(error or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _workspace_quality_repair_path_key(path: str) -> str:
    """Case-fold official CMakeLists.txt so leftover remint can claim docs owner."""

    token = str(path or "").strip().replace("\\", "/")
    if not token:
        return ""
    name = token.rsplit("/", 1)[-1]
    if name.lower() == "cmakelists.txt":
        parent = token[: -len(name)] if token.endswith(name) else ""
        return f"{parent}cmakelists.txt"
    return token


def _workspace_quality_authoritative_owner_paths(
    metadata: Mapping[str, Any],
    *,
    run_id: str,
) -> list[str]:
    """Project declared scope and committed effects into repair ownership.

    PM target paths may intentionally remain generic while Chief Engineer
    topology expands them into concrete package files.  The run-bound JobToken
    is the capability authority consumed by physical tools; ignoring it makes
    Factory lease a causal source repair to an unrelated PM row.

    A Director may also materialize a file through a durable, authoritative
    effect receipt that was not present in the original CE topology (for
    example a language entrypoint selected during execution).  That committed
    effect is stronger ownership evidence than a later disk scan.  Reuse only
    successful, non-no-op receipt paths from the same run-bound TaskRuntime row;
    never infer authority from an arbitrary file merely because it exists.
    """

    paths: list[str] = []
    completion_raw = metadata.get("task_completion_projection")
    completion = completion_raw if isinstance(completion_raw, Mapping) else {}
    completion_run_id = str(completion.get("run_id") or "").strip()
    owned_artifacts = completion.get("owned_artifacts")
    if not completion_run_id or completion_run_id == run_id:
        for artifact in owned_artifacts if isinstance(owned_artifacts, list | tuple) else ():
            if not isinstance(artifact, Mapping):
                continue
            owner_task_id = str(artifact.get("owner_task_id") or "").strip()
            projection_task_id = str(completion.get("task_id") or "").strip()
            if owner_task_id and projection_task_id and owner_task_id != projection_task_id:
                continue
            path = str(artifact.get("path") or "").strip()
            if path:
                paths.append(path)

    # JobToken scope is capability authority, not unique artifact ownership:
    # dependency manifests/entrypoints can legitimately be writable by more
    # than one task. Prefer the CE completion projection when it names owned
    # artifacts; use token paths only for legacy rows that lack that SSoT.
    if not paths:
        for key in ("control_plane_job_token", "capability_token"):
            raw_token = metadata.get(key)
            token = raw_token if isinstance(raw_token, Mapping) else {}
            token_run_id = str(token.get("factory_run_id") or token.get("run_id") or "").strip()
            if token_run_id and token_run_id != run_id:
                continue
            for path_key in ("allowed_write_paths", "target_files"):
                raw_paths = token.get(path_key)
                if isinstance(raw_paths, str):
                    paths.append(raw_paths)
                elif isinstance(raw_paths, list | tuple | set):
                    paths.extend(str(item) for item in raw_paths)
    adapter_result_raw = metadata.get("adapter_result")
    adapter_result = adapter_result_raw if isinstance(adapter_result_raw, Mapping) else {}
    batch_receipt_raw = adapter_result.get("batch_receipt")
    batch_receipt = batch_receipt_raw if isinstance(batch_receipt_raw, Mapping) else {}
    raw_results = batch_receipt.get("raw_results")
    for raw_result in raw_results if isinstance(raw_results, list | tuple) else ():
        if not isinstance(raw_result, Mapping) or str(raw_result.get("status") or "").strip() != "success":
            continue
        result_raw = raw_result.get("result")
        result = result_raw if isinstance(result_raw, Mapping) else {}
        receipt_raw = raw_result.get("effect_receipt")
        if not isinstance(receipt_raw, Mapping):
            receipt_raw = result.get("effect_receipt")
        receipt = receipt_raw if isinstance(receipt_raw, Mapping) else {}
        if receipt.get("authoritative") is not True or str(receipt.get("receipt_outcome") or "") != "succeeded":
            continue
        before_hash = str(result.get("before_sha256") or "").strip()
        after_hash = str(result.get("after_sha256") or "").strip()
        if not before_hash or not after_hash or before_hash == after_hash:
            continue
        effect_path = str(result.get("file") or result.get("path") or "").strip()
        if effect_path:
            paths.append(effect_path)
    return _dedupe_workspace_repair_paths(paths)


def _workspace_quality_frozen_ce_owner_task(
    executor: Any,
    *,
    run_id: str,
    task_id: str,
    canonical_task: Mapping[str, Any],
) -> dict[str, Any]:
    """Rehydrate one drained owner from its immutable same-run CE handoff.

    PM tasks can deliberately name only a manifest while CE expands the
    concrete package topology. After terminal TaskRuntime drain, rebuilding a
    repair owner from PM paths alone loses the JobToken authority used by the
    original Director effects. Reuse only an exact, generated, handoff-ready
    CE row whose blueprint and JobToken are bound to this run and task.

    Invalid or incomplete evidence returns the untouched PM task. The caller
    will therefore remain fail-closed unless the PM task itself owns the
    diagnostic target.
    """

    review = executor._load_chief_engineer_review_payload(run_id=run_id)
    review_rows = review.get("blueprints")
    matching_rows = [
        row
        for row in (review_rows if isinstance(review_rows, list) else ())
        if isinstance(row, Mapping) and str(row.get("task_id") or "").strip() == task_id
    ]
    if len(matching_rows) != 1:
        return dict(canonical_task)
    review_row = matching_rows[0]
    if str(review_row.get("status") or "").strip() != "generated" or review_row.get("handoff_ready") is not True:
        return dict(canonical_task)
    blueprint_id = str(review_row.get("blueprint_id") or "").strip()
    blueprint_path = str(review_row.get("blueprint_path") or "").strip().replace("\\", "/")
    if not blueprint_id or not blueprint_path.startswith("runtime/blueprints/"):
        return dict(canonical_task)
    blueprint = executor._read_json_artifact_payload(blueprint_path)
    if (
        str(blueprint.get("task_id") or "").strip() != task_id
        or str(blueprint.get("blueprint_id") or "").strip() != blueprint_id
        or str(blueprint.get("status") or "").strip() != "generated"
        or blueprint.get("handoff_ready") is not True
    ):
        return dict(canonical_task)

    job_token_raw = blueprint.get("job_token")
    job_token = dict(job_token_raw) if isinstance(job_token_raw, Mapping) else {}
    token_run_id = str(job_token.get("factory_run_id") or job_token.get("run_id") or "").strip()
    token_id = str(job_token.get("token_id") or "").strip()
    blueprint_hash = str(blueprint.get("blueprint_hash") or "").strip()
    if (
        token_run_id != run_id
        or not token_id
        or len(blueprint_hash) != 64
        or str(job_token.get("blueprint_hash") or "").strip() != blueprint_hash
    ):
        return dict(canonical_task)

    blueprint_targets = _dedupe_workspace_repair_paths(list(blueprint.get("target_files") or ()))
    token_targets = _dedupe_workspace_repair_paths(list(job_token.get("target_files") or ()))
    allowed_write_paths = _dedupe_workspace_repair_paths(list(job_token.get("allowed_write_paths") or ()))
    if (
        not blueprint_targets
        or not set(blueprint_targets).issubset(token_targets)
        or not set(blueprint_targets).issubset(allowed_write_paths)
    ):
        return dict(canonical_task)

    capability_raw = blueprint.get("capability_token")
    capability = dict(capability_raw) if isinstance(capability_raw, Mapping) else dict(job_token)
    capability_run_id = str(capability.get("factory_run_id") or capability.get("run_id") or "").strip()
    if (
        capability_run_id != run_id
        or str(capability.get("token_id") or "").strip() != token_id
        or str(capability.get("blueprint_hash") or "").strip() != blueprint_hash
    ):
        return dict(canonical_task)

    restored = dict(canonical_task)
    metadata_raw = restored.get("metadata")
    metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
    metadata.update(
        {
            "blueprint_id": blueprint_id,
            "blueprint_path": blueprint_path,
            "runtime_blueprint_path": blueprint_path,
            "control_plane_job_token": job_token,
            "capability_token": capability,
            "project_completion_contract": blueprint.get("project_completion_contract"),
            "project_completion_contract_hash": blueprint.get("project_completion_contract_hash"),
            "workspace_quality_frozen_owner_authority": {
                "schema_version": "factory.workspace-quality-frozen-owner-authority.v1",
                "factory_run_id": run_id,
                "task_id": task_id,
                "blueprint_id": blueprint_id,
                "blueprint_path": blueprint_path,
                "blueprint_hash": blueprint_hash,
                "job_token_id": token_id,
            },
        }
    )
    restored.update(
        {
            "target_files": blueprint_targets,
            "scope_paths": blueprint_targets,
            "blueprint_id": blueprint_id,
            "blueprint_path": blueprint_path,
            "runtime_blueprint_path": blueprint_path,
            "metadata": metadata,
        }
    )
    return restored


def _workspace_quality_test_shortfall_owner_targets(
    executor: Any,
    *,
    run_id: str,
    artifact_quality_errors: list[str],
) -> list[str]:
    """Resolve pathless test-depth residuals to their CE/JobToken owner.

    Test discovery/depth diagnostics often contain only counts (``Ran 0
    tests``, ``test_source_files=1 < 2``) and therefore yield no file path.
    Falling back to generic changed production files leases the wrong Director
    task.  Rehydrate each exact same-run CE owner and return only declared test
    artifacts; PM scope remains the fail-closed fallback when CE authority is
    unavailable.
    """

    blob = "\n".join(str(item or "") for item in artifact_quality_errors)
    if _TEST_MODALITY_SHORTFALL_RE.search(blob) is None:
        return []

    targets: list[str] = []
    for canonical_task in executor._load_pm_plan_tasks("tasks/plan.json"):
        if not isinstance(canonical_task, Mapping):
            continue
        task_id = str(canonical_task.get("id") or canonical_task.get("task_id") or "").strip()
        if not task_id:
            continue
        owner_task = _workspace_quality_frozen_ce_owner_task(
            executor,
            run_id=run_id,
            task_id=task_id,
            canonical_task=canonical_task,
        )
        raw_targets = owner_task.get("target_files") or owner_task.get("scope_paths") or ()
        normalized_targets = _dedupe_workspace_repair_paths(
            [raw_targets] if isinstance(raw_targets, str) else list(raw_targets)
        )
        targets.extend(path for path in normalized_targets if _is_workspace_quality_test_target(path))
    return _dedupe_workspace_repair_paths(targets)


def _workspace_quality_repair_path_overlaps(normalized_targets: set[str], candidate_paths: set[str]) -> set[str]:
    """Intersect repair targets with owner paths, treating CMakeLists case aliases as one file."""

    candidate_keys = {_workspace_quality_repair_path_key(path): path for path in candidate_paths}
    overlaps: set[str] = set()
    for target in normalized_targets:
        key = _workspace_quality_repair_path_key(target)
        owner_path = candidate_keys.get(key)
        if owner_path is not None:
            overlaps.add(owner_path)
    return overlaps


def _claim_workspace_quality_repair_attempt(
    executor,
    *,
    run: FactoryRun,
    repair_attempt: int,
    target_files: list[str],
) -> tuple[str, int, TaskRuntimeExecutionAttemptIdentityV1, dict[str, Any]]:
    """Claim the Director attempt that owns one post-verifier repair round.

    Reopen the exact owning task when one exists.  If no canonical PM/CE
    owner can be resolved, fail closed instead of minting a helper task:
    verifier repair must remain a continuation of real Director work, not a
    fresh authority that QA invents after the fact. Workspace verification used to invoke
    the guarded Director role without
    a TaskRuntime execution attempt.  Directed-effect validation therefore
    rejected the turn before the model could edit the failed artifacts.  A
    repair round is real Director work: give it a fresh, run-bound task row,
    propagate its exact session identity to roles.runtime, and terminally
    settle it after the repair result is known.  PM and CE are intentionally
    not restarted for this local verifier failure.
    """

    run_id = run.id
    task_runtime = TaskRuntimeService(str(executor.workspace))
    normalized_targets = {
        str(path or "").strip().replace("\\", "/") for path in target_files if str(path or "").strip()
    }
    # javac `class X is public, should be declared in X.java` leftover names
    # the official basename. The owning task still lists melodymodel.java.
    # Score the same-directory existing .java siblings so claim does not
    # raise workspace_quality_repair_canonical_owner_missing.
    workspace_root = Path(executor.workspace)
    for rel in list(normalized_targets):
        if not rel.endswith(".java"):
            continue
        parent = workspace_root / Path(rel).parent
        if not parent.is_dir():
            continue
        try:
            for sibling in parent.glob("*.java"):
                normalized_targets.add(sibling.relative_to(workspace_root).as_posix())
        except (OSError, ValueError):
            continue

    def row_owner_score(candidate: Mapping[str, Any]) -> tuple[int, int]:
        return executor._workspace_quality_repair_owner_score(
            candidate,
            run_id=run_id,
            normalized_targets=normalized_targets,
        )

    owner_rows = [
        candidate
        for candidate in task_runtime.list_task_rows(include_terminal=True)
        if row_owner_score(candidate)[0] > 0
    ]
    owner_row = max(owner_rows, key=row_owner_score) if owner_rows else None
    if owner_row is None:
        # A terminal Factory drain deliberately removes live TaskRuntime rows
        # after freezing their authority.  A QA-only retry preserves that
        # frozen epoch, so restore the exact PM task contract named by it
        # before claiming a local Director repair.  Without this bridge the QA
        # boundary can validate the frozen projection, but the repair claimant
        # sees no live owner and fails before the Provider/tool layer runs.
        from polaris.cells.factory.pipeline.public.contracts import (
            FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
            FactoryTerminalTaskRuntimeProjectionV1,
        )

        frozen_payload = run.metadata.get(FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY)
        frozen_task_statuses: dict[str, str] = {}
        if isinstance(frozen_payload, Mapping):
            frozen = FactoryTerminalTaskRuntimeProjectionV1.from_dict(frozen_payload)
            if frozen.factory_run_id != run_id:
                raise RuntimeError("workspace_quality_repair_frozen_authority_run_mismatch")
            rows = frozen.projection.get("rows")
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, Mapping):
                    continue
                external_task_id = str(row.get("external_task_id") or "").strip()
                row_factory_run_id = str(row.get("factory_run_id") or "").strip()
                if external_task_id and not external_task_id.startswith("factory-") and row_factory_run_id == run_id:
                    frozen_task_statuses[external_task_id] = str(
                        row.get("execution_state") or row.get("status") or ""
                    ).strip()

        canonical_tasks: dict[str, dict[str, Any]] = {}
        frozen_candidates: list[dict[str, Any]] = []
        for index, task in enumerate(executor._load_pm_plan_tasks("tasks/plan.json"), start=1):
            task_id = executor._task_id(task, index)
            if not task_id or task_id not in frozen_task_statuses:
                continue
            canonical_task = _workspace_quality_frozen_ce_owner_task(
                executor,
                run_id=run_id,
                task_id=task_id,
                canonical_task=task,
            )
            canonical_tasks[task_id] = canonical_task
            metadata_raw = canonical_task.get("metadata")
            metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
            metadata.update(
                {
                    "external_task_id": task_id,
                    "pm_task_id": task_id,
                    "source_task_id": task_id,
                    "factory_run_id": run_id,
                    "factory_stage": "quality_gate",
                    "source_artifact": "tasks/plan.json",
                    "task_contract": canonical_task,
                }
            )
            for key in ("scope", "scope_paths", "target_files", "acceptance", "acceptance_criteria", "steps"):
                if key in canonical_task:
                    metadata[key] = canonical_task[key]
            frozen_candidate = {
                **canonical_task,
                "external_task_id": task_id,
                "status": frozen_task_statuses[task_id],
                "metadata": metadata,
            }
            if row_owner_score(frozen_candidate)[0] > 0:
                frozen_candidates.append(frozen_candidate)

        if frozen_candidates:
            frozen_owner = max(frozen_candidates, key=row_owner_score)
            frozen_owner_id = str(frozen_owner.get("external_task_id") or "").strip()
            materialized = executor._materialize_pm_plan_taskboard(
                [canonical_tasks[frozen_owner_id]],
                run_id=run_id,
                source_stage="quality_gate",
                run_metadata=run.metadata,
            )
            binding_failures = materialized.get("binding_failures")
            if binding_failures:
                raise RuntimeError("workspace_quality_repair_frozen_owner_binding_failed")
            # ``_materialize_pm_plan_taskboard`` owns a separate service
            # instance. Reopen TaskRuntime here so this claimant cannot retain
            # the pre-restore empty board cache and report ``task_not_found``.
            # ``get_task`` is the fact-only observer and can hide the restored
            # terminal owner after COMPLETED_VERIFIED drain; mutation restore
            # must walk ``list_task_rows(include_terminal=True)``.
            task_runtime = TaskRuntimeService(str(executor.workspace))
            restored_row = None
            for candidate in task_runtime.list_task_rows(include_terminal=True):
                if not isinstance(candidate, Mapping):
                    continue
                candidate_metadata = candidate.get("metadata")
                candidate_metadata = candidate_metadata if isinstance(candidate_metadata, Mapping) else {}
                candidate_external = str(
                    candidate_metadata.get("external_task_id") or candidate.get("external_task_id") or ""
                ).strip()
                if candidate_external == frozen_owner_id:
                    restored_row = candidate
                    break
            if not isinstance(restored_row, Mapping):
                raise RuntimeError("workspace_quality_repair_frozen_owner_restore_failed")
            owner_row = restored_row
    if owner_row is not None:
        owner_metadata = owner_row.get("metadata")
        owner_metadata = owner_metadata if isinstance(owner_metadata, Mapping) else {}
        external_task_id = str(
            owner_metadata.get("external_task_id") or owner_row.get("external_task_id") or ""
        ).strip()
        task_row_id = task_runtime.normalize_task_id(owner_row.get("id"))
        if task_row_id is None:
            raise RuntimeError("workspace_quality_repair_owner_task_id_invalid")
        owner_status = str(owner_row.get("status") or owner_row.get("raw_status") or "").strip().lower()
        if owner_status in {"completed", "failed", "cancelled", "blocked"}:
            reopened = task_runtime.reopen_task_row(
                task_row_id,
                reason="workspace_quality_gate_failed",
                metadata={
                    "factory_run_id": run_id,
                    "workspace_quality_repair": True,
                    "repair_attempt": repair_attempt,
                },
            )
            if not isinstance(reopened, Mapping) or str(reopened.get("status") or "").lower() not in {
                "pending",
                "ready",
                "blocked",
            }:
                raise RuntimeError("workspace_quality_repair_owner_reopen_failed")
        repair_task = dict(owner_row)
        repair_task_metadata = dict(owner_metadata)
    else:
        raise RuntimeError("workspace_quality_repair_canonical_owner_missing")
    # The quality-repair adapter must receive the original Director task
    # contract, not a synthetic ``target_files`` shell.  Final-request
    # qualification reconstructs authoritative PM/CE evidence from this
    # row (including blueprint_id/runtime_blueprint_path).  Dropping the
    # owner metadata made a valid local retry fail closed with
    # missing_required_refs=pm_contract,ce_blueprint after QA had already
    # reopened the task.
    repair_task["id"] = external_task_id
    repair_task["task_id"] = external_task_id
    repair_task["external_task_id"] = external_task_id
    for key in (
        "goal",
        "description",
        "scope",
        "scope_paths",
        "target_files",
        "acceptance",
        "acceptance_criteria",
        "verification_commands",
        "blueprint_id",
        "runtime_blueprint_path",
        "blueprint_path",
    ):
        if not repair_task.get(key) and repair_task_metadata.get(key) is not None:
            repair_task[key] = repair_task_metadata[key]
    authoritative_owner_paths = _workspace_quality_authoritative_owner_paths(
        repair_task_metadata,
        run_id=run_id,
    )
    if authoritative_owner_paths:
        # CE topology + run-bound JobToken is more specific than the PM's
        # generic placeholder targets.  Reuse it for the local retry so the
        # roles adapter and DEO authorize the same owner selected above.
        repair_task["target_files"] = authoritative_owner_paths
        repair_task["scope_paths"] = authoritative_owner_paths
        repair_task_metadata["workspace_quality_repair_authoritative_owner_targets"] = authoritative_owner_paths
    elif not repair_task.get("target_files"):
        repair_task["target_files"] = sorted(normalized_targets)
    repair_task_metadata.update(
        {
            "external_task_id": external_task_id,
            "factory_run_id": run_id,
            "workspace_quality_repair": True,
            "repair_attempt": repair_attempt,
        }
    )
    repair_task["metadata"] = repair_task_metadata
    binding = bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(executor.workspace),
            task_id=external_task_id,
            factory_run_id=run_id,
        )
    )
    if not binding.ok:
        raise RuntimeError(f"workspace_quality_repair_binding_failed:{binding.code}")
    claim = task_runtime.claim_execution(
        task_row_id,
        worker_id="director",
        role_id="director",
        run_id=run_id,
        lease_ttl_seconds=_WORKSPACE_QUALITY_REPAIR_LEASE_TTL_SECONDS,
        selection_source="factory_stage_executor.workspace_quality_repair",
        external_task_id=external_task_id,
        context_summary="director_workspace_quality_repair",
        metadata={
            "factory_run_id": run_id,
            "factory_stage": "quality_gate",
            "workspace_quality_repair": True,
            "repair_attempt": repair_attempt,
            "execution_identity_required": True,
        },
    )
    session = claim.get("session") if isinstance(claim, dict) else None
    attempt_record = claim.get("execution_attempt") if isinstance(claim, dict) else None
    if not isinstance(session, Mapping) or not isinstance(attempt_record, Mapping) or not bool(claim.get("success")):
        reason = str(claim.get("reason") or "unknown") if isinstance(claim, dict) else "invalid_claim_result"
        raise RuntimeError(f"workspace_quality_repair_claim_failed:{reason}")
    execution_attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(attempt_record)
    return external_task_id, task_row_id, execution_attempt, repair_task


def _apply_workspace_quality_repairs(
    executor,
    *,
    run_id: str,
    artifact_quality_errors: list[str],
    task_id: str | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
    repair_task: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule,
    )

    class _QualityRepairAdapter:
        def __init__(self, workspace: Path) -> None:
            self.workspace = str(workspace)
            self._execution = SimpleNamespace(_message_bus=None)

        def _update_task_progress(
            self,
            task_id: str,
            phase: str,
            current_file: str | None = None,
            event_code: str | None = None,
            event_status: str | None = None,
            event_reason: str | None = None,
            event_detail: str | None = None,
            event_refs: dict[str, Any] | None = None,
        ) -> None:
            del task_id, phase, current_file, event_code, event_status, event_reason, event_detail, event_refs

    task_payload = dict(repair_task) if isinstance(repair_task, Mapping) else {}
    task_metadata = task_payload.get("metadata")
    task_metadata = dict(task_metadata) if isinstance(task_metadata, Mapping) else {}
    raw_owned_targets = task_payload.get("target_files") or task_metadata.get("target_files") or ()
    owned_targets = _dedupe_workspace_repair_paths(
        [raw_owned_targets] if isinstance(raw_owned_targets, str) else list(raw_owned_targets)
    )
    target_files = owned_targets or executor._workspace_quality_repair_target_files()
    if not target_files:
        target_files = executor._workspace_quality_repair_diagnostic_target_files(artifact_quality_errors)
    if not target_files:
        target_files = executor._workspace_quality_repair_changed_files()
    if "package.json" not in target_files and (executor.workspace / "package.json").is_file():
        target_files = [*target_files, "package.json"]
    metadata: dict[str, Any] = {
        **task_metadata,
        "target_files": target_files,
        "delivery_mode": "materialize_changes",
    }
    blueprint_artifact, blueprint_text = executor._workspace_quality_repair_blueprint_evidence(run_id=run_id)
    if not task_payload:
        # Compatibility-only workspace invocation. Canonical Factory retries
        # pass ``repair_task`` and remain constrained to that exact PM/CE owner.
        metadata["factory_workspace_quality_repair"] = {
            "ce_blueprint_artifact": blueprint_artifact,
            "target_files": target_files,
            "run_id": run_id,
        }
    if blueprint_text:
        blueprint_payload = {
            "schema_version": "factory.workspace_quality_repair.ce_blueprint_context.v1",
            "artifact": blueprint_artifact,
            "evidence": blueprint_text,
        }
        metadata["ce_blueprint"] = blueprint_payload
        metadata["chief_engineer_blueprint"] = blueprint_payload
        metadata["chief_engineer_blueprint_evidence"] = blueprint_text
    resolved_task_id = str(task_id or "").strip() or f"factory-quality-gate:{run_id}"
    if task_payload:
        task_payload["target_files"] = target_files
        task_payload["metadata"] = metadata
    else:
        task_payload = {"target_files": target_files, "metadata": metadata}
    return run_director_materialization_quality_repair_schedule(
        _QualityRepairAdapter(executor.workspace),
        task=task_payload,
        task_id=resolved_task_id,
        artifact_quality_errors=artifact_quality_errors,
        execution_attempt=execution_attempt,
    )


async def _apply_workspace_quality_deterministic_repairs(
    executor,
    *,
    run: FactoryRun,
    artifact_quality_errors: list[str],
    repair_attempt: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute one deterministic repair round on its owning Director task.

    Runtime repair planners intentionally emit deferred DEO effects; they do
    not write merely because a plan is plannable.  Workspace QA previously
    called the planner without a canonical TaskRuntime attempt and without
    committing the deferred effects.  Every executable rule therefore
    collapsed to ``deo_deferred_repair_attempt_required`` and the quality
    gate needlessly fell through to another LLM turn.

    Keep ordinary verifier failures on Director: claim/reopen the best owning
    task, defer through the repair kernel, commit through DEO, settle that
    exact attempt, then let the caller re-run only the failed verifier set.
    PM and Chief Engineer are not restarted.
    """

    from polaris.cells.roles.adapters.public import commit_materialization_deferred_repairs
    from polaris.cells.runtime.task_runtime.public import (
        create_task_runtime_execution_attempt_authority,
    )

    run_id = str(run.id or "").strip() or "workspace-quality-repair"
    # Claim the task that owns the *current verifier diagnostic*, not the task
    # with the largest share of all project targets.  The previous code passed
    # the complete project target set into the owner scorer, so a later
    # ``src/engine/*.rs`` compiler failure could repeatedly reopen TASK-1 merely
    # because TASK-1 owned more files.  That erased same-task locality and made
    # ordinary Director code repair look like a cross-task CE contract gap.
    diagnostic_target_files = _workspace_quality_causal_repair_target_files(
        executor,
        artifact_quality_errors=artifact_quality_errors,
    )
    if not diagnostic_target_files:
        diagnostic_target_files = _workspace_quality_test_shortfall_owner_targets(
            executor,
            run_id=run_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    # One g++ blob lists every residual path. Scoring all of them reopened
    # TASK-1-source-models-2 (poem.hpp) while the leading error was
    # Moon.status on generator.cpp (live L1-06). Claim the first diagnostic
    # path's owner; later residuals select the next owner after revalidation.
    primary_diagnostic_targets = _workspace_quality_llm_claim_target_files(
        owner_target_files=None,
        diagnostic_target_files=diagnostic_target_files,
        fallback_target_files=[],
    )
    claim_target_files = (
        primary_diagnostic_targets
        or diagnostic_target_files
        or executor._director_stage_materialization_settle_target_files(diagnostics=artifact_quality_errors)
    )
    try:
        task_id, task_row_id, execution_attempt, repair_task = executor._claim_workspace_quality_repair_attempt(
            run=run,
            repair_attempt=repair_attempt,
            target_files=claim_target_files,
        )
        authority = create_task_runtime_execution_attempt_authority(execution_attempt)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return [], {
            "attempted": True,
            "success": False,
            "repair_mode": "director_deterministic",
            "error": f"workspace_quality_deterministic_attempt_claim_failed:{exc}",
            "source_tools": ["director_runtime_repair_attempt_error"],
            "tool_results": 0,
            "write_tool_evidence": False,
        }

    owner_metadata_raw = repair_task.get("metadata")
    owner_metadata = dict(owner_metadata_raw) if isinstance(owner_metadata_raw, Mapping) else {}
    raw_owner_targets = repair_task.get("target_files") or owner_metadata.get("target_files") or ()
    owner_target_files = _dedupe_workspace_repair_paths(
        [raw_owner_targets] if isinstance(raw_owner_targets, str) else list(raw_owner_targets)
    )
    owner_target_set = set(owner_target_files)
    in_scope_diagnostic_targets = [path for path in diagnostic_target_files if path in owner_target_set]
    out_of_scope_diagnostic_targets = [path for path in diagnostic_target_files if path not in owner_target_set]
    task_boundary_owner_evidence = {
        "schema_version": "factory.workspace_quality_task_owner.v1",
        "source": "task_runtime_execution_attempt",
        "task_id": task_id,
        "owner_target_files": owner_target_files[:80],
        "diagnostic_target_files": diagnostic_target_files[:20],
        "in_scope_diagnostic_target_files": in_scope_diagnostic_targets[:20],
        "out_of_scope_diagnostic_target_files": out_of_scope_diagnostic_targets[:20],
        # One verifier command can report failures from several PM tasks in a
        # single stderr payload.  Keep this round on the claimed task's exact
        # diagnostic subset, re-run the verifier, then let the residual select
        # the next owner.  Requiring one task to own *every* path turned a
        # normal two-task compiler batch into a false CE escalation.
        "director_local_repair_allowed": bool(in_scope_diagnostic_targets),
    }

    results: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    receipts: list[dict[str, Any]] = []
    heartbeat_stop = asyncio.Event()
    heartbeat_failures: list[dict[str, Any]] = []
    heartbeat_task = asyncio.create_task(
        _run_workspace_quality_repair_heartbeat(
            authority,
            stop=heartbeat_stop,
            failures=heartbeat_failures,
            context_summary="director_workspace_quality_deterministic_repair",
        )
    )
    try:
        results, raw_summary = await asyncio.to_thread(
            executor._apply_workspace_quality_repairs,
            run_id=run_id,
            artifact_quality_errors=artifact_quality_errors,
            task_id=task_id,
            execution_attempt=execution_attempt,
            repair_task=repair_task,
        )
        summary = dict(raw_summary)
        summary["task_id"] = task_id
        summary["task_boundary_owner_evidence"] = task_boundary_owner_evidence
        deferred_candidates = [
            item
            for item in results
            if isinstance(item, Mapping)
            and isinstance(item.get("result"), Mapping)
            and (
                item["result"].get("deferred_request") is not None
                or str(item["result"].get("status") or "").strip()
                in {"deferred_repair_effects_pending", "deferred_command_effect_pending"}
            )
        ]
        commit_context = executor._director_stage_materialization_settle_commit_context(
            run=run,
            run_id=run_id,
            diagnostics=artifact_quality_errors,
            factory_stage="quality_gate",
        )
        for candidate_index, candidate in enumerate(deferred_candidates):
            candidate_receipts = await commit_materialization_deferred_repairs(
                workspace=str(execution_attempt.workspace),
                tool_results=[candidate],
                execution_attempt=execution_attempt,
                execution_attempt_authority=authority,
                turn_id=(f"workspace-quality-repair-{run_id}-round{repair_attempt}-candidate{candidate_index}"),
                context=commit_context,
            )
            receipts.extend(dict(item) for item in candidate_receipts if isinstance(item, Mapping))
    except Exception as exc:  # noqa: BLE001 - fail closed at DEO commit boundary.
        summary = {
            **summary,
            "attempted": True,
            "success": False,
            "repair_mode": "director_deterministic",
            "error": f"workspace_quality_deterministic_commit_failed:{type(exc).__name__}:{exc}",
        }
    summary.setdefault("task_id", task_id)
    summary.setdefault("task_boundary_owner_evidence", task_boundary_owner_evidence)
    # ``create_task`` does not guarantee the heartbeat coroutine runs before a
    # very fast repair callback returns.  Yield once so an immediate authority
    # rejection becomes part of this transaction decision instead of being
    # discovered only after the verifier has already started.
    await asyncio.sleep(0)
    if heartbeat_failures:
        summary["execution_attempt_heartbeat_failures"] = heartbeat_failures
        summary.setdefault(
            "error",
            f"workspace_quality_repair_lease_heartbeat_failed:{heartbeat_failures[0]['code']}",
        )

    successful_receipts = [
        item for item in receipts if executor._director_stage_materialization_receipt_succeeded(item)
    ]
    failed_receipts = [
        item for item in receipts if not executor._director_stage_materialization_receipt_succeeded(item)
    ]
    # Lease liveness is part of the write authority.  A physical receipt that
    # lands after heartbeat rejection/expiry cannot complete the task because
    # this Director no longer proves exclusive ownership of the attempt.
    mutation_committed = bool(successful_receipts) and not heartbeat_failures
    pending_attempt: dict[str, Any] | None = None
    if mutation_committed:
        pending_attempt = {
            "task_id": task_id,
            "task_row_id": task_row_id,
            "execution_attempt": execution_attempt,
            "heartbeat_task": heartbeat_task,
            "heartbeat_stop": heartbeat_stop,
            "heartbeat_failures": heartbeat_failures,
        }
        task_runtime_attempt = {
            "task_id": task_id,
            "session_id": execution_attempt.session_id,
            "settled": False,
            "outcome": "pending_revalidation",
        }
    else:
        task_runtime_attempt = (
            await _settle_pending_workspace_quality_repair_attempt(
                executor,
                {
                    "task_id": task_id,
                    "task_row_id": task_row_id,
                    "execution_attempt": execution_attempt,
                    "heartbeat_task": heartbeat_task,
                    "heartbeat_stop": heartbeat_stop,
                    "heartbeat_failures": heartbeat_failures,
                },
                accepted=False,
                reason=str(summary.get("error") or "workspace_quality_deterministic_repair_no_commit"),
            )
            or {}
        )
    evidence = (
        [f"deferred_commit:successful={len(successful_receipts)};failed={len(failed_receipts)}"]
        if mutation_committed
        else []
    )
    summary.update(
        {
            "attempted": True,
            "success": mutation_committed,
            "repair_mode": "director_deterministic",
            "tool_results": len(results),
            "committed_receipt_count": len(successful_receipts),
            "failed_receipt_count": len(failed_receipts),
            "write_tool_evidence": mutation_committed,
            "evidence": evidence,
            "task_runtime_repair_attempt": task_runtime_attempt,
        }
    )
    if pending_attempt is not None:
        summary["_pending_task_runtime_repair_attempt"] = pending_attempt
    return results, summary


def _workspace_quality_llm_claim_target_files(
    *,
    owner_target_files: list[str] | None,
    diagnostic_target_files: list[str],
    fallback_target_files: list[str],
) -> list[str]:
    """Choose the current claimed owner before unrelated mixed diagnostics.

    An owner path is authoritative only when the current verifier diagnostics
    also name it.  This preserves a TaskRuntime claim such as TASK-3 owning a
    discovered-test failure while still rejecting stale owner scope after the
    causal diagnostic moves to another task.  Without an intersecting current
    owner, prefer the first non-test path after causal ordering because test or
    import wrappers commonly precede their implementation source.
    """

    normalized_diagnostics = _dedupe_workspace_repair_paths(diagnostic_target_files)
    if normalized_diagnostics:
        normalized_owners = _dedupe_workspace_repair_paths(owner_target_files or [])
        diagnostic_set = set(normalized_diagnostics)
        # The deterministic TaskRuntime claim passes only its verified
        # in-scope diagnostic targets.  A generic prior owner override often
        # contains additional files that are absent from the current failure;
        # treating that broader scope as authoritative would reintroduce stale
        # owner routing.  Require the whole supplied owner set to be current.
        if normalized_owners and set(normalized_owners).issubset(diagnostic_set):
            owner_set = set(normalized_owners)
            return [path for path in normalized_diagnostics if path in owner_set][:1]
        source_targets = [path for path in normalized_diagnostics if not _is_workspace_quality_test_target(path)]
        return (source_targets or normalized_diagnostics)[:1]
    if owner_target_files:
        return _dedupe_workspace_repair_paths(owner_target_files)
    return _dedupe_workspace_repair_paths(fallback_target_files)


def _workspace_quality_causal_repair_target_files(
    executor,
    *,
    artifact_quality_errors: list[str],
) -> list[str]:
    """Combine direct verifier paths with Director's causal source discovery.

    This function is deliberately read-only.  The returned paths are evidence
    for selecting a canonical CE-owned task; they do not grant write scope.
    The subsequent TaskRuntime claim and JobToken remain authoritative.
    """

    from polaris.cells.roles.adapters.public import resolve_director_causal_quality_repair_target_files

    direct_targets = executor._workspace_quality_repair_diagnostic_target_files(artifact_quality_errors)
    causal_targets = resolve_director_causal_quality_repair_target_files(
        artifact_quality_errors=list(artifact_quality_errors),
        changed_files=executor._workspace_quality_repair_changed_files(),
        workspace_full=str(executor.workspace),
    )
    diagnostic_blob = "\n".join(str(item or "") for item in artifact_quality_errors)
    causal_go_sources = [
        path
        for path in causal_targets
        if Path(path).suffix.casefold() == ".go" and not _is_workspace_quality_test_target(path)
    ]
    # A runnable Go test assertion names the observing ``*_test.go`` wrapper,
    # not necessarily the implementation that owns the behavior.  Keeping
    # that wrapper in the owner-candidate set lets an existing test-task lease
    # win before the causal source paths are considered.  Live L3-22 then spent
    # repeated TASK-3 turns rewriting already-compiling tests while validation,
    # physics, and error-priority defects remained in production packages.
    #
    # Preserve direct test ownership whenever a real compiler location exists:
    # syntax/type failures in authored tests still belong to the test task.
    # Only assertion-only ``go test`` residuals move to causal implementation
    # sources discovered by the read-only Director target resolver.
    if (
        causal_go_sources
        and "--- fail:" in diagnostic_blob.casefold()
        and _GO_COMPILER_DIAGNOSTIC_RE.search(diagnostic_blob) is None
    ):
        direct_sources = [path for path in direct_targets if not _is_workspace_quality_test_target(path)]
        return _dedupe_workspace_repair_paths([*causal_go_sources, *direct_sources])
    return _dedupe_workspace_repair_paths([*direct_targets, *causal_targets])


async def _apply_workspace_quality_llm_repairs(
    executor,
    *,
    run: FactoryRun,
    context: dict[str, Any],
    artifact_quality_errors: list[str],
    repair_attempt: int,
    interface_discrepancy_evidence: dict[str, Any] | None = None,
    owner_target_files: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    run_id = run.id
    changed_files = executor._workspace_quality_repair_changed_files()
    if not changed_files:
        return [], {
            "attempted": False,
            "repair_mode": "director_llm",
            "reason": "no_workspace_source_files_for_repair",
            "source_tools": [],
            "tool_results": 0,
        }
    declared_target_files = executor._workspace_quality_repair_target_files()
    diagnostic_target_files = _workspace_quality_causal_repair_target_files(
        executor,
        artifact_quality_errors=artifact_quality_errors,
    )
    materialized_declared_targets = [path for path in declared_target_files if path in set(changed_files)]
    claim_target_files = _workspace_quality_llm_claim_target_files(
        owner_target_files=owner_target_files,
        diagnostic_target_files=diagnostic_target_files,
        fallback_target_files=materialized_declared_targets or changed_files,
    )
    # Provider mutation intent follows the exact current diagnostic target.
    # The claimed task/JobToken below remains the full authorization boundary;
    # this narrow list prevents an obsolete owner override from hiding the
    # file the verifier actually proved faulty.
    target_files = claim_target_files or materialized_declared_targets or changed_files
    inbound_quality_context = context.get("director_quality_repair")
    inbound_regression_guards: list[str] = []
    inbound_causal_reanalysis_required = False
    if isinstance(inbound_quality_context, Mapping):
        raw_guards = inbound_quality_context.get("regression_guard_errors")
        if isinstance(raw_guards, list | tuple):
            inbound_regression_guards = [
                str(item or "").strip() for item in raw_guards if str(item or "").strip()
            ][:6]
        # This wrapper intentionally rebuilds the Director context from a
        # whitelist so inbound data cannot override paths/tool policy. Preserve
        # the one bounded Factory-owned escalation bit explicitly. Live L3-22
        # set it after verified stagnation, but dropping it here meant the
        # final provider request never received the causal-path directive.
        inbound_causal_reanalysis_required = (
            inbound_quality_context.get("causal_reanalysis_required") is True
        )
    repair_context: dict[str, Any] = {
        "delivery_mode": "materialize_changes",
        "run_id": run_id,
        "factory_run_id": run_id,
        "target_files": (target_files or changed_files)[:80],
        "changed_files": changed_files[:80],
        # Quality repair is an executable Director turn. ``auto`` allowed the
        # provider to return prose (r52: "Should proceed: False") even though
        # the prompt required a physical edit. Force at least one native tool;
        # the transaction kernel still permits read_file first and then keeps
        # the same turn alive until an authorized edit receipt or hard blocker.
        "_transaction_kernel_forced_tool_choice": "required",
        "director_quality_repair": {
            "repair_target_files": (target_files or changed_files)[:80],
            "write_only_single_target": (
                {"target_file": (target_files or changed_files)[0]} if len(target_files or changed_files) == 1 else None
            ),
        },
        "factory_workspace_quality_repair": {
            "changed_files": changed_files[:80],
            "target_files": target_files[:80],
        },
    }
    if inbound_regression_guards:
        repair_context["director_quality_repair"]["regression_guard_errors"] = inbound_regression_guards
    if inbound_causal_reanalysis_required:
        repair_context["director_quality_repair"]["causal_reanalysis_required"] = True
    catalog = executor._read_catalog_contract()
    primary_language = str(catalog.get("primary_language") or "").strip()
    project_type = str(catalog.get("project_type") or "").strip()
    if primary_language:
        repair_context.setdefault("language", primary_language)
        repair_context.setdefault("programming_language", primary_language)
        repair_context.setdefault("tech_stack", {"language": primary_language})
    if project_type:
        repair_context.setdefault("project_type", project_type)
        repair_context.setdefault("project_kind", project_type)
    blueprint_artifact, blueprint_text = executor._workspace_quality_repair_blueprint_evidence(run_id=run_id)
    if blueprint_text:
        blueprint_payload = {
            "schema_version": "factory.workspace_quality_repair.ce_blueprint_context.v1",
            "artifact": blueprint_artifact,
            "evidence": blueprint_text,
        }
        repair_context["ce_blueprint"] = blueprint_payload
        repair_context["chief_engineer_blueprint"] = blueprint_payload
        repair_context["chief_engineer_blueprint_evidence"] = blueprint_text
        repair_context["factory_workspace_quality_repair"]["ce_blueprint_artifact"] = blueprint_artifact
    if interface_discrepancy_evidence:
        repair_context["director_interface_discrepancy_retry"] = {
            "authorized": executor._workspace_quality_interface_discrepancy_allows_director_retry(
                interface_discrepancy_evidence
            ),
            "recommended_owner": interface_discrepancy_evidence.get("recommended_owner"),
            "recommended_route": interface_discrepancy_evidence.get("recommended_route"),
            "reason": interface_discrepancy_evidence.get("reason"),
            "interface_discrepancy_evidence": interface_discrepancy_evidence,
        }
        repair_context["factory_task_boundary_interface_discrepancy"] = interface_discrepancy_evidence
        repair_context["factory_workspace_quality_repair"]["interface_discrepancy_evidence"] = (
            interface_discrepancy_evidence
        )
    for key in (
        "language",
        "prompt_language",
        "programming_language",
        "artifact",
        "artifact_type",
        "project_kind",
        "prompt_profile_ids",
        "prompt_profiles",
        "prompt_profile",
        "prompt_profile_id",
    ):
        if key in context:
            repair_context[key] = context[key]
    # Quality repair is a child execution of the same Factory run, not an
    # unbounded ad-hoc role call.  Preserve the parent deadline and TaskRuntime
    # wall-clock budget so roles.adapters can keep the provider timeout narrow
    # while allowing the already-started tool/DEO transaction to settle.  L1-01
    # r42 dropped these fields, reported ``no_factory_deadline``, then marked a
    # task failed even though its write receipt committed moments later.
    for key in (
        "factory_run_deadline_epoch_seconds",
        "factory_run_deadline_source",
        "factory_run_timeout_seconds",
        "factory_director_execution_deadline_epoch_seconds",
        "request_timeout_seconds",
    ):
        if key in context:
            repair_context[key] = context[key]
    try:
        (
            repair_task_id,
            repair_task_row_id,
            execution_attempt,
            repair_task,
        ) = executor._claim_workspace_quality_repair_attempt(
            run=run,
            repair_attempt=repair_attempt,
            target_files=claim_target_files or target_files or changed_files,
        )
        from polaris.cells.runtime.task_runtime.public import (
            create_task_runtime_execution_attempt_authority,
        )

        execution_attempt_authority = create_task_runtime_execution_attempt_authority(execution_attempt)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return [], {
            "attempted": True,
            "repair_mode": "director_llm",
            "success": False,
            "error": f"workspace_quality_repair_attempt_claim_failed:{exc}",
            "source_tools": ["director_materialization_quality_repair_error"],
            "tool_results": 0,
        }

    repair_context["task_id"] = repair_task_id
    repair_context["session_id"] = execution_attempt.session_id
    repair_context["task_runtime_execution_attempt"] = execution_attempt
    repair_context["task_runtime_execution_attempt_authority"] = execution_attempt_authority
    repair_metadata = repair_context.get("metadata")
    if not isinstance(repair_metadata, dict):
        repair_metadata = {}
        repair_context["metadata"] = repair_metadata
    repair_metadata["task_id"] = repair_task_id
    repair_metadata.setdefault("factory_run_id", run_id)
    repair_metadata.setdefault("run_id", run_id)
    # Session id alone is reused across quality repair rounds on the same
    # owner. Transaction invocation identity then minted the same
    # ``txi_*-0`` turn_outcomes key and same-run retries collided
    # (live L2-14: append_fact_event idempotency conflict on TASK-2).
    # Both authoritative scope keys must carry the same composite so
    # ``_resolve_execution_scope`` does not see a disagreement.
    quality_execution_scope_id = (
        f"{execution_attempt.session_id}:a{execution_attempt.attempt}"
        f":wq{repair_attempt}:{execution_attempt.lease_expires_at}"
    )
    repair_metadata["execution_attempt_id"] = quality_execution_scope_id
    repair_metadata["task_runtime_session_id"] = quality_execution_scope_id
    repair_context["task_runtime_session_id"] = quality_execution_scope_id
    repair_metadata["workspace_quality_repair"] = True
    heartbeat_stop = asyncio.Event()
    heartbeat_failures: list[dict[str, Any]] = []
    heartbeat_task = asyncio.create_task(
        _run_workspace_quality_repair_heartbeat(
            execution_attempt_authority,
            stop=heartbeat_stop,
            failures=heartbeat_failures,
            context_summary="director_workspace_quality_llm_repair",
        )
    )
    try:
        from polaris.cells.roles.adapters.public.service import run_director_materialization_quality_repair

        results, summary = await run_director_materialization_quality_repair(
            str(executor.workspace),
            task=repair_task,
            target_task_id=repair_task_id,
            run_id=run_id,
            context=repair_context,
            original_message=executor._workspace_quality_repair_original_message(
                run_id=run_id,
                target_files=target_files,
            ),
            llm_call_timeout=executor._workspace_quality_llm_repair_timeout_seconds(context),
            artifact_quality_errors=artifact_quality_errors,
            changed_files=changed_files,
            repair_attempt=repair_attempt,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed around external LLM repair boundary.
        error_text = str(exc)
        results = []
        summary = {
            "attempted": True,
            "repair_mode": "director_llm",
            "success": False,
            "error": error_text,
            "error_code": (
                "quality_repair_provider_timeout"
                if "request timeout" in error_text.casefold()
                else "quality_repair_invoke_failed"
            ),
            "source_tools": ["director_materialization_quality_repair_error"],
            "tool_results": 0,
        }
    # Give a newly scheduled heartbeat one turn before deciding whether the
    # mutation may enter verifier-owned pending state.  This closes the
    # fast-provider-response race exercised by the direct adapter tests.
    await asyncio.sleep(0)
    normalized_summary = dict(summary)
    if not str(normalized_summary.get("error_code") or "").strip():
        returned_error = str(normalized_summary.get("error") or "").strip()
        returned_error_folded = returned_error.casefold()
        if (
            "request timeout" in returned_error_folded
            or "provider_stream_timeout" in returned_error_folded
            or "llm_timeout" in returned_error_folded
        ):
            # The Director adapter can return a structured failed summary
            # instead of raising across the Factory boundary. Live L3-22 did
            # this twice: TransactionKernel Request timeout, then
            # director_quality_repair_2_llm_timeout. Normalize both here so a
            # transport failure cannot masquerade as semantic no-mutation.
            normalized_summary["error_code"] = "quality_repair_provider_timeout"
    if heartbeat_failures:
        normalized_summary["execution_attempt_heartbeat_failures"] = heartbeat_failures
        normalized_summary.setdefault(
            "error",
            f"workspace_quality_repair_lease_heartbeat_failed:{heartbeat_failures[0]['code']}",
        )
    normalized_summary["repair_mode"] = "director_llm"
    raw_source_tools = normalized_summary.get("source_tools")
    source_tool_items = raw_source_tools if isinstance(raw_source_tools, list | tuple | set) else []
    source_tools = [str(item) for item in source_tool_items if str(item or "").strip()]
    if results and "director_materialization_quality_repair" not in source_tools:
        source_tools.append("director_materialization_quality_repair")
    normalized_summary["source_tools"] = source_tools
    normalized_summary.setdefault("tool_results", len(results))
    normalized_summary.setdefault("attempted", True)
    normalized_summary.setdefault("task_id", repair_task_id)
    if interface_discrepancy_evidence:
        authorized_retry = executor._workspace_quality_interface_discrepancy_allows_director_retry(
            interface_discrepancy_evidence
        )
        normalized_summary.setdefault("interface_discrepancy_evidence", interface_discrepancy_evidence)
        normalized_summary["task_boundary_interface_discrepancy_retry_authorized"] = authorized_retry
        metadata_raw = interface_discrepancy_evidence.get("metadata")
        interface_metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        owner_evidence = interface_metadata.get("task_boundary_owner_evidence")
        if isinstance(owner_evidence, Mapping):
            normalized_summary.setdefault("task_boundary_owner_evidence", dict(owner_evidence))
    mutation_committed = not heartbeat_failures and any(
        executor._workspace_quality_repair_result_has_mutation(dict(item))
        for item in results
        if isinstance(item, Mapping)
    )
    if mutation_committed:
        normalized_summary["_pending_task_runtime_repair_attempt"] = {
            "task_id": repair_task_id,
            "task_row_id": repair_task_row_id,
            "execution_attempt": execution_attempt,
            "heartbeat_task": heartbeat_task,
            "heartbeat_stop": heartbeat_stop,
            "heartbeat_failures": heartbeat_failures,
        }
        normalized_summary["task_runtime_repair_attempt"] = {
            "task_id": repair_task_id,
            "session_id": execution_attempt.session_id,
            "settled": False,
            "outcome": "pending_revalidation",
        }
    else:
        normalized_summary["task_runtime_repair_attempt"] = (
            await _settle_pending_workspace_quality_repair_attempt(
                executor,
                {
                    "task_id": repair_task_id,
                    "task_row_id": repair_task_row_id,
                    "execution_attempt": execution_attempt,
                    "heartbeat_task": heartbeat_task,
                    "heartbeat_stop": heartbeat_stop,
                    "heartbeat_failures": heartbeat_failures,
                },
                accepted=False,
                reason=str(normalized_summary.get("error") or "workspace_quality_repair_no_mutation"),
            )
            or {}
        )
    return [dict(item) for item in results], normalized_summary


async def _run_workspace_quality_checks(executor, run: FactoryRun, context: dict[str, Any]) -> tuple[bool, str]:
    commands = executor._workspace_quality_commands(context)
    task_boundary_blocker = executor._workspace_quality_task_boundary_blocker(run, context)
    depth_result = (
        None if task_boundary_blocker else executor._workspace_quality.delivery_depth_contract_result(context)
    )
    if not task_boundary_blocker and not commands and depth_result is None:
        return True, ""

    configured_timeout_seconds = float(
        context.get("workspace_validation_timeout_seconds") or _WORKSPACE_VALIDATION_TIMEOUT_SECONDS
    )
    results: list[dict[str, Any]] = []
    repair_summary: dict[str, Any] = {
        "attempted": False,
        "success": False,
        "source_tools": [],
        "tool_results": 0,
        "rounds": [],
    }

    def write_workspace_validation_failure(
        reason_code: str,
        detail: str,
        *,
        repair_override: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        payload = {
            "schema_version": "factory.workspace_quality_checks.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "factory_stage_executor",
            "factory_run_id": run.id,
            "workspace": str(executor.workspace),
            "passed": False,
            "commands": results,
            "repair": repair_override if repair_override is not None else repair_summary,
            "warnings": [reason_code],
            "error": detail,
            "deadline": {
                "remaining_seconds": executor._factory_deadline_remaining_seconds(context),
                "deadline_epoch_seconds": context.get("factory_run_deadline_epoch_seconds"),
                "timeout_seconds": context.get("factory_run_timeout_seconds"),
                "source": context.get("factory_run_deadline_source"),
            },
        }
        if extra_payload:
            payload.update(dict(extra_payload))
        artifact = executor._write_workspace_validation_artifact(run, context, payload)
        return False, artifact

    if task_boundary_blocker:
        reason_code = str(
            task_boundary_blocker.get("reason_code") or "factory_quality_gate_task_boundary_incomplete_materialization"
        )
        detail = str(task_boundary_blocker.get("detail") or reason_code)
        repair_override = {
            "attempted": False,
            "success": False,
            "source_tools": [],
            "tool_results": 0,
            "reason": "task_boundary_not_ready",
            "task_boundary_blocker": task_boundary_blocker,
        }
        return write_workspace_validation_failure(
            reason_code,
            detail,
            repair_override=repair_override,
            extra_payload={
                "failure_class": task_boundary_blocker.get("failure_class"),
                "responsible_layer": task_boundary_blocker.get("responsible_layer"),
                "task_boundary_blocker": task_boundary_blocker,
                "commands_skipped": True,
                "skip_reason": reason_code,
            },
        )

    def workspace_checks_deadline_blocker(phase: str) -> str:
        remaining_seconds = executor._factory_deadline_remaining_seconds(context)
        if remaining_seconds is None:
            return ""
        minimum_remaining = _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS + _QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS
        if remaining_seconds >= minimum_remaining:
            return ""
        return (
            f"Workspace quality checks stopped at {phase} because the factory run deadline has only "
            f"{remaining_seconds:.1f}s remaining and QA requires at least {minimum_remaining:.1f}s"
        )

    def workspace_quality_command_timeout_seconds() -> float:
        remaining_seconds = executor._factory_deadline_remaining_seconds(context)
        if remaining_seconds is None:
            return configured_timeout_seconds
        reserved_for_qa = _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS + _QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS
        available_for_command = max(1.0, remaining_seconds - reserved_for_qa)
        return max(1.0, min(configured_timeout_seconds, available_for_command))

    async def run_workspace_quality_command_with_deadline(
        command: list[str],
        phase: str,
    ) -> tuple[dict[str, Any], str]:
        deadline_detail = workspace_checks_deadline_blocker(f"before_{phase}")
        if deadline_detail:
            return {}, deadline_detail
        command_timeout = workspace_quality_command_timeout_seconds()
        result = await asyncio.to_thread(executor._run_workspace_quality_command, command, command_timeout)
        result["phase"] = phase
        if command_timeout < configured_timeout_seconds:
            result["deadline_capped_timeout_seconds"] = command_timeout
            result["configured_timeout_seconds"] = configured_timeout_seconds
        return result, ""

    def workspace_repair_deadline_blocker(phase: str) -> str:
        remaining_seconds = executor._factory_deadline_remaining_seconds(context)
        if remaining_seconds is None:
            return ""
        if remaining_seconds >= _WORKSPACE_QUALITY_REPAIR_MIN_LLM_START_BUDGET_SECONDS:
            return ""
        return (
            f"Workspace quality repair skipped at {phase} because the factory run deadline has only "
            f"{remaining_seconds:.1f}s remaining"
        )

    initial_deadline_detail = workspace_checks_deadline_blocker("before_prepare")
    if initial_deadline_detail:
        return write_workspace_validation_failure(
            "factory_quality_gate_workspace_checks_deadline_insufficient",
            initial_deadline_detail,
        )

    prepare_commands = executor._workspace_quality_prepare_commands(commands, context)
    prepare_failed = False
    for command in prepare_commands:
        result, deadline_detail = await run_workspace_quality_command_with_deadline(command, "prepare")
        if deadline_detail:
            return write_workspace_validation_failure(
                "factory_quality_gate_workspace_checks_deadline_insufficient",
                deadline_detail,
            )
        results.append(result)
        if not bool(result.get("passed")):
            prepare_failed = True

    run_commands = [] if prepare_failed else commands
    for command in run_commands:
        result, deadline_detail = await run_workspace_quality_command_with_deadline(command, "check")
        if deadline_detail:
            return write_workspace_validation_failure(
                "factory_quality_gate_workspace_checks_deadline_insufficient",
                deadline_detail,
            )
        results.append(result)
    if not prepare_failed and depth_result is not None:
        depth_result["phase"] = "check"
        results.append(depth_result)
    if prepare_failed:
        for command in commands:
            results.append(
                {
                    "command": command,
                    "phase": "check",
                    "exit_code": None,
                    "passed": False,
                    "error": "skipped because workspace validation preparation failed",
                    "stdout_tail": "",
                    "stderr_tail": "",
                }
            )

    repair_errors: list[str] = []
    repair_results: list[dict[str, Any]] = []

    rerun_prepare_results: list[dict[str, Any]] = []
    rerun_results: list[dict[str, Any]] = []
    if run_commands and not prepare_failed and not all(bool(item.get("passed")) for item in results):
        max_rounds = int(context.get("workspace_quality_repair_max_rounds") or _WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS)
        max_rounds = max(1, min(max_rounds, _WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS))
        latest_check_results = [item for item in results if str(item.get("phase") or "") == "check"]
        repair_rounds: list[dict[str, Any]] = []
        source_tools: list[str] = []
        evidence: list[str] = []
        write_tool_evidence = False
        task_boundary_triage_required = False
        task_boundary_triage_summary: dict[str, Any] = {}
        consecutive_stagnant_rounds = 0
        nonprogress_rounds_since_last_progress = 0
        last_nonprogress_effect = ""
        last_nonprogress_task_id = ""
        convergence_stop_reason = ""
        seen_diagnostic_error_codes: set[str] = set()
        seen_plannable_source_tools: set[str] = set()
        regression_guard_errors: list[str] = []
        regression_synthesis_round_granted = False
        regression_synthesis_round_pending = False
        regression_synthesis_union_test_identities: set[str] = set()
        semantic_contract_conflict_candidate: dict[str, Any] = {}
        causal_reanalysis_round_granted = False
        causal_reanalysis_round_pending = False
        provider_transport_retry_granted = False
        provider_transport_retry_pending = False
        # One diagnostic signature gets at most one materialization-schedule
        # probe that reaches the canonical TaskRuntime claim boundary.  The
        # materialization schedule contains callback labels which are not
        # represented by ``plannable_source_tools``, so the read-only plan
        # probe alone cannot safely prove that the schedule is a no-op.  Once
        # a live attempt returns without an authoritative mutation receipt,
        # however, repeating that same deterministic schedule against the
        # unchanged verifier signature only reopens/settles the same task and
        # burns CPU/attempt epochs.  Route subsequent identical diagnostics
        # directly to the same-owner LLM edit path instead.
        deterministic_no_commit_contexts: dict[tuple[str, ...], dict[str, Any]] = {}

        def llm_repair_context() -> dict[str, Any]:
            """Attach bounded prior-round failures without changing write authority."""

            if not regression_guard_errors and not causal_reanalysis_this_round:
                return context
            projected = dict(context)
            raw_quality = projected.get("director_quality_repair")
            quality = dict(raw_quality) if isinstance(raw_quality, Mapping) else {}
            if regression_guard_errors:
                quality["regression_guard_errors"] = list(regression_guard_errors[:6])
            if causal_reanalysis_this_round:
                quality["causal_reanalysis_required"] = True
            projected["director_quality_repair"] = quality
            return projected

        def current_workspace_repair_summary(
            *,
            residual_errors: list[str] | None = None,
            deadline_detail: str = "",
        ) -> dict[str, Any]:
            partial_summary = {
                "attempted": bool(repair_rounds),
                "success": False,
                "revalidated": bool(rerun_results),
                "residual_error_count": len(residual_errors or []),
                "residual_errors": (residual_errors or [])[:10],
                "director_runtime_repair_coverage": executor._workspace_quality_repair_coverage_report(
                    residual_errors or []
                ),
                "plan_probe_preaudit": executor._workspace_quality_repair_plan_probe_report(residual_errors or []),
                "source_tools": list(dict.fromkeys(source_tools)),
                "tool_results": len(repair_results),
                "write_tool_evidence": write_tool_evidence,
                "artifact_quality_errors": repair_errors[:10],
                "evidence": evidence[:12],
                "max_rounds": max_rounds,
                "rounds": repair_rounds,
                "consecutive_stagnant_rounds": consecutive_stagnant_rounds,
                "nonprogress_rounds_since_last_progress": nonprogress_rounds_since_last_progress,
                "convergence_stop_reason": convergence_stop_reason,
                "regression_synthesis_round_granted": regression_synthesis_round_granted,
                "causal_reanalysis_round_granted": causal_reanalysis_round_granted,
                "provider_transport_retry_granted": provider_transport_retry_granted,
            }
            if semantic_contract_conflict_candidate:
                partial_summary["semantic_contract_conflict_candidate"] = dict(
                    semantic_contract_conflict_candidate
                )
            if deadline_detail:
                partial_summary["deadline_blocker"] = deadline_detail
            scope_filter = workspace_quality_latest_task_boundary_scope_filter(partial_summary)
            if scope_filter:
                partial_summary["task_boundary_scope_filter"] = scope_filter
            if task_boundary_triage_required:
                partial_summary.update(
                    {
                        "task_boundary_triage_required": True,
                        "success_reason": "task_boundary_interface_discrepancy_required",
                        "plan_probe_preaudit": task_boundary_triage_summary.get("plan_probe_preaudit"),
                        "interface_discrepancy_evidence": task_boundary_triage_summary.get(
                            "interface_discrepancy_evidence"
                        ),
                    }
                )
            return partial_summary

        forced_next_owner_targets: list[str] = []
        leftover_extra_pending = False
        for round_index in range(max_rounds + 2):
            if not leftover_rotate_allows_quality_extra_round(
                round_index=round_index,
                max_rounds=max_rounds,
                leftover_extra_pending=(leftover_extra_pending or provider_transport_retry_pending),
            ):
                break
            leftover_extra_pending = False
            provider_transport_retry_pending = False
            causal_reanalysis_this_round = causal_reanalysis_round_pending
            causal_reanalysis_round_pending = False
            owner_override = list(forced_next_owner_targets) or None
            forced_next_owner_targets = []
            if latest_check_results and all(bool(item.get("passed")) for item in latest_check_results):
                break
            repair_errors = executor._workspace_quality_repair_errors(latest_check_results or results)
            if not repair_errors:
                break
            before_check_results = [dict(item) for item in (latest_check_results or results)]
            deadline_detail = workspace_repair_deadline_blocker(f"before_repair_round_{round_index + 1}")
            if deadline_detail:
                return write_workspace_validation_failure(
                    "factory_quality_gate_workspace_repair_deadline_insufficient",
                    deadline_detail,
                    repair_override=current_workspace_repair_summary(
                        residual_errors=repair_errors,
                        deadline_detail=deadline_detail,
                    ),
                )
            if owner_override is None and round_index == 0:
                # Live L2-15 remint-11/12: leftover only ran AFTER a TU
                # stagnated, so the first four rounds never leased the
                # unclosed queue.hpp that produced ProjectNS::std.
                # Later rounds must not re-seed claimed=[] (that bounced
                # remint-22 back onto src/main.cpp after a type-home rotate).
                seeded = workspace_quality_unclaimed_failing_tu_targets(
                    repair_errors,
                    claimed_targets=[],
                    workspace=Path(executor.workspace),
                )
                if seeded:
                    owner_override = seeded
            before_signature = executor._workspace_quality_diagnostic_signature(repair_errors)
            deterministic_probe_signature = _workspace_quality_deterministic_probe_signature(repair_errors)
            round_plan_probe = executor._workspace_quality_repair_plan_probe_report(repair_errors)
            # Oscillation uses AFTER codes from completed rounds only.
            # Seeding `seen` with this round's before-set made C++
            # forward_unmask (undeclared -> missing-member, kinds still
            # overlapping) look like a return to an already-seen code and
            # tripped the breaker after two real unmasks (live L1-06).
            cached_deterministic_context = deterministic_no_commit_contexts.get(deterministic_probe_signature)
            deterministic_skipped_repeated_no_commit = cached_deterministic_context is not None
            round_repair_results: list[dict[str, Any]]
            round_summary: dict[str, Any]
            if deterministic_skipped_repeated_no_commit:
                round_repair_results = []
                round_summary = {
                    "attempted": False,
                    "success": False,
                    "repair_mode": "director_deterministic",
                    "skipped_reason": "same_diagnostic_signature_previously_produced_no_commit",
                    "source_tools": [],
                    "tool_results": 0,
                    "write_tool_evidence": False,
                    "evidence": ["deterministic_no_commit_signature_cache_hit"],
                }
                # The cache suppresses only the repeated deterministic
                # execution.  Preserve the current read-only plan probe and
                # immutable TaskRuntime owner evidence so a recognized-but-
                # unplannable diagnostic can still route to the same Director
                # LLM owner.  Live L3-22 lost this evidence on cache hit:
                # rounds 5/6 returned no tool results and were incorrectly
                # charged as convergence failures after round 3 had already
                # reduced the verifier set from two tests to one.
                round_summary["plan_probe_preaudit"] = dict(round_plan_probe)
                if isinstance(cached_deterministic_context, Mapping):
                    for key in ("task_id", "task_boundary_owner_evidence"):
                        value = cached_deterministic_context.get(key)
                        if value is not None:
                            round_summary[key] = value
                if str(round_plan_probe.get("status") or "") == "coverage_matched_but_unplannable":
                    round_summary.update(
                        {
                            "stage": "runtime_plan_probe_unplannable",
                            "success_reason": "task_boundary_interface_discrepancy_required",
                        }
                    )
            else:
                round_repair_results, round_summary = await executor._apply_workspace_quality_deterministic_repairs(
                    run=run,
                    artifact_quality_errors=repair_errors,
                    repair_attempt=round_index + 1,
                )
            round_requires_task_boundary_triage = executor._workspace_quality_summary_requires_task_boundary_triage(
                dict(round_summary)
            )
            round_repair_evidence = executor._workspace_quality_repair_evidence(round_repair_results)
            round_write_tool_evidence = any(
                executor._workspace_quality_repair_result_has_mutation(item) for item in round_repair_results
            ) or bool(round_summary.get("write_tool_evidence"))
            if not deterministic_skipped_repeated_no_commit and not round_write_tool_evidence:
                deterministic_no_commit_contexts[deterministic_probe_signature] = {
                    key: round_summary.get(key)
                    for key in ("task_id", "task_boundary_owner_evidence")
                    if round_summary.get(key) is not None
                }
            raw_round_summary_evidence = round_summary.get("evidence")
            llm_repair_attempted_in_round = False
            claimed_round_owner_targets = _workspace_quality_claimed_owner_diagnostic_targets(round_summary)
            round_llm_owner_targets = claimed_round_owner_targets or owner_override
            hold_llm_for_plannable_deterministic = _workspace_quality_hold_llm_for_plannable_deterministic(
                round_plan_probe,
                write_tool_evidence=round_write_tool_evidence,
                residual_errors=repair_errors,
            )
            if hold_llm_for_plannable_deterministic:
                round_summary = dict(round_summary)
                round_summary["held_llm_for_plannable_deterministic_repair"] = True
            if isinstance(raw_round_summary_evidence, list | tuple):
                round_repair_evidence.extend(
                    str(item) for item in raw_round_summary_evidence if str(item or "").strip()
                )
            if not hold_llm_for_plannable_deterministic and round_requires_task_boundary_triage:
                interface_discrepancy_evidence = executor._workspace_quality_interface_discrepancy_evidence(
                    dict(round_summary),
                    repair_errors,
                )
                if executor._workspace_quality_interface_discrepancy_allows_director_retry(
                    interface_discrepancy_evidence
                ):
                    claimed_owner_targets = executor._workspace_quality_claimed_owner_repair_targets(
                        interface_discrepancy_evidence
                    )
                    deterministic_noop_summary = dict(round_summary)
                    deadline_detail = workspace_repair_deadline_blocker(
                        f"before_interface_discrepancy_llm_repair_round_{round_index + 1}"
                    )
                    if deadline_detail:
                        return write_workspace_validation_failure(
                            "factory_quality_gate_workspace_repair_deadline_insufficient",
                            deadline_detail,
                            repair_override=current_workspace_repair_summary(
                                residual_errors=repair_errors,
                                deadline_detail=deadline_detail,
                            ),
                        )
                    round_repair_results, round_summary = await executor._apply_workspace_quality_llm_repairs(
                        run=run,
                        context=llm_repair_context(),
                        artifact_quality_errors=repair_errors,
                        repair_attempt=round_index + 1,
                        interface_discrepancy_evidence=interface_discrepancy_evidence,
                        owner_target_files=round_llm_owner_targets or claimed_owner_targets or None,
                    )
                    llm_repair_attempted_in_round = True
                    if not round_repair_results:
                        round_summary = dict(round_summary)
                        round_summary["deterministic_no_materialized_evidence"] = deterministic_noop_summary
                    round_requires_task_boundary_triage = (
                        executor._workspace_quality_summary_requires_task_boundary_triage(dict(round_summary))
                    )
                    round_repair_evidence = executor._workspace_quality_repair_evidence(round_repair_results)
                    round_write_tool_evidence = any(
                        executor._workspace_quality_repair_result_has_mutation(item) for item in round_repair_results
                    )
            owned_round_targets = [
                str(item or "").strip()
                for item in (round_summary.get("repair_target_files") or [])
                if str(item or "").strip()
            ]
            if (
                not hold_llm_for_plannable_deterministic
                and not round_requires_task_boundary_triage
                and (round_repair_results or owned_round_targets)
                and not round_write_tool_evidence
                and not llm_repair_attempted_in_round
            ):
                # Logs, coverage notes, or failed/no-op tool rows are evidence
                # of an attempt, not evidence of repair progress. Only an
                # authoritative workspace mutation may suppress the same-task
                # LLM edit fallback. r46 returned a deterministic source_tool
                # plus evidence but mutated nothing; this old condition skipped
                # the LLM twice and tripped stagnation with the failing source
                # untouched.
                deterministic_noop_summary = dict(round_summary)
                deadline_detail = workspace_repair_deadline_blocker(f"before_llm_repair_round_{round_index + 1}")
                if deadline_detail:
                    return write_workspace_validation_failure(
                        "factory_quality_gate_workspace_repair_deadline_insufficient",
                        deadline_detail,
                        repair_override=current_workspace_repair_summary(
                            residual_errors=repair_errors,
                            deadline_detail=deadline_detail,
                        ),
                    )
                round_repair_results, round_summary = await executor._apply_workspace_quality_llm_repairs(
                    run=run,
                    context=llm_repair_context(),
                    artifact_quality_errors=repair_errors,
                    repair_attempt=round_index + 1,
                    owner_target_files=round_llm_owner_targets,
                )
                if not round_repair_results:
                    round_summary = dict(round_summary)
                    round_summary["deterministic_no_materialized_evidence"] = deterministic_noop_summary
                round_requires_task_boundary_triage = executor._workspace_quality_summary_requires_task_boundary_triage(
                    dict(round_summary)
                )
            elif (
                not hold_llm_for_plannable_deterministic
                and not round_requires_task_boundary_triage
                and not round_repair_results
                and not llm_repair_attempted_in_round
            ):
                deadline_detail = workspace_repair_deadline_blocker(f"before_llm_repair_round_{round_index + 1}")
                if deadline_detail:
                    return write_workspace_validation_failure(
                        "factory_quality_gate_workspace_repair_deadline_insufficient",
                        deadline_detail,
                        repair_override=current_workspace_repair_summary(
                            residual_errors=repair_errors,
                            deadline_detail=deadline_detail,
                        ),
                    )
                round_repair_results, round_summary = await executor._apply_workspace_quality_llm_repairs(
                    run=run,
                    context=llm_repair_context(),
                    artifact_quality_errors=repair_errors,
                    repair_attempt=round_index + 1,
                    owner_target_files=round_llm_owner_targets,
                )
                round_requires_task_boundary_triage = executor._workspace_quality_summary_requires_task_boundary_triage(
                    dict(round_summary)
                )
            if (
                not hold_llm_for_plannable_deterministic
                and not llm_repair_attempted_in_round
                and not round_requires_task_boundary_triage
                and _workspace_quality_residuals_miss_mutated_paths(
                    repair_errors,
                    round_repair_results,
                    owner_paths=_workspace_quality_round_owner_paths(round_summary),
                )
            ):
                # Live L2-14: materialization wrote a rust helper/manifest and
                # suppressed LLM even though E0573/E0277 residuals lived in
                # other owner files. Mutation evidence must cover residual paths.
                deadline_detail = workspace_repair_deadline_blocker(
                    f"before_uncovered_residual_llm_repair_round_{round_index + 1}"
                )
                if deadline_detail:
                    return write_workspace_validation_failure(
                        "factory_quality_gate_workspace_repair_deadline_insufficient",
                        deadline_detail,
                        repair_override=current_workspace_repair_summary(
                            residual_errors=repair_errors,
                            deadline_detail=deadline_detail,
                        ),
                    )
                round_repair_results, round_summary = await executor._apply_workspace_quality_llm_repairs(
                    run=run,
                    context=llm_repair_context(),
                    artifact_quality_errors=repair_errors,
                    repair_attempt=round_index + 1,
                    owner_target_files=round_llm_owner_targets,
                )
                llm_repair_attempted_in_round = True
                round_requires_task_boundary_triage = executor._workspace_quality_summary_requires_task_boundary_triage(
                    dict(round_summary)
                )
            deferred_owner_targets = executor._workspace_quality_deferred_owner_targets(dict(round_summary))
            if deferred_owner_targets and not hold_llm_for_plannable_deterministic:
                # Target inference happens inside the Director adapter after the
                # first TaskRuntime owner has been claimed. If the precise
                # verifier targets belong to a different PM task, the adapter
                # correctly refuses the write and returns structured ownership
                # evidence. Rebind once to that exact owner instead of failing
                # the chain or restarting PM/CE.
                deferred_summary = executor._workspace_quality_repair_summary_projection(
                    dict(round_summary),
                    repair_errors,
                )
                deadline_detail = workspace_repair_deadline_blocker(
                    f"before_deferred_owner_rebind_round_{round_index + 1}"
                )
                if deadline_detail:
                    return write_workspace_validation_failure(
                        "factory_quality_gate_workspace_repair_deadline_insufficient",
                        deadline_detail,
                        repair_override=current_workspace_repair_summary(
                            residual_errors=repair_errors,
                            deadline_detail=deadline_detail,
                        ),
                    )
                round_repair_results, round_summary = await executor._apply_workspace_quality_llm_repairs(
                    run=run,
                    context=llm_repair_context(),
                    artifact_quality_errors=repair_errors,
                    repair_attempt=round_index + 1,
                    # Live L2-15 remint-14: leftover FAILING_TUS seed filled
                    # owner_override first, so ``owner_override or deferred``
                    # never leased the handoff header (queue.hpp / models).
                    owner_target_files=deferred_owner_targets or owner_override,
                )
                round_summary = dict(round_summary)
                round_summary["deferred_owner_rebind"] = {
                    "attempted": True,
                    "target_files": deferred_owner_targets,
                    "previous_repair": deferred_summary,
                }
                round_requires_task_boundary_triage = executor._workspace_quality_summary_requires_task_boundary_triage(
                    dict(round_summary)
                )
            if deterministic_skipped_repeated_no_commit:
                round_summary = dict(round_summary)
                repeated_no_commit_evidence = [
                    str(item) for item in (round_summary.get("evidence") or ()) if str(item or "").strip()
                ]
                if "deterministic_no_commit_signature_cache_hit" not in repeated_no_commit_evidence:
                    repeated_no_commit_evidence.append("deterministic_no_commit_signature_cache_hit")
                round_summary["evidence"] = repeated_no_commit_evidence
                round_summary["deterministic_repair_skipped"] = {
                    "reason": "same_diagnostic_signature_previously_produced_no_commit",
                    "task_runtime_claimed": False,
                }
            cpp_post_repair_results: list[dict[str, Any]] = []
            if not round_requires_task_boundary_triage:
                cpp_post_repair_results = await asyncio.to_thread(executor._apply_workspace_quality_cpp_post_repairs)
            if cpp_post_repair_results:
                round_repair_results.extend(cpp_post_repair_results)
                round_summary = dict(round_summary)
                round_summary_tools = [
                    str(item) for item in round_summary.get("source_tools", []) if str(item or "").strip()
                ]
                if "deterministic_cpp_post_repair" not in round_summary_tools:
                    round_summary_tools.append("deterministic_cpp_post_repair")
                round_summary["source_tools"] = round_summary_tools
            repair_results.extend(round_repair_results)
            normalized_round_summary = dict(round_summary)
            pending_round_attempt = normalized_round_summary.pop(
                "_pending_task_runtime_repair_attempt",
                None,
            )
            round_source_tools = [
                str(item) for item in normalized_round_summary.get("source_tools", []) if str(item or "").strip()
            ]
            round_evidence = executor._workspace_quality_repair_evidence(round_repair_results)
            round_write_tool_evidence = any(
                executor._workspace_quality_repair_result_has_mutation(item) for item in round_repair_results
            ) or bool(normalized_round_summary.get("write_tool_evidence"))
            raw_round_summary_evidence = normalized_round_summary.get("evidence")
            if isinstance(raw_round_summary_evidence, list | tuple):
                round_evidence.extend(str(item) for item in raw_round_summary_evidence if str(item or "").strip())
            source_tools.extend(round_source_tools)
            evidence.extend(round_evidence)
            write_tool_evidence = write_tool_evidence or round_write_tool_evidence
            summary_projection = executor._workspace_quality_repair_summary_projection(
                normalized_round_summary,
                repair_errors,
            )
            round_payload: dict[str, Any] = {
                "round": round_index + 1,
                "attempted": True,
                "artifact_quality_errors": repair_errors[:10],
                "regression_guard_errors": regression_guard_errors[:6],
                "director_runtime_repair_coverage": executor._workspace_quality_repair_coverage_report(repair_errors),
                "plan_probe_preaudit": round_plan_probe,
                "tool_results": len(round_repair_results),
                "source_tools": round_source_tools,
                "write_tool_evidence": round_write_tool_evidence,
                "evidence": round_evidence,
            }
            if causal_reanalysis_this_round:
                round_payload["causal_reanalysis_required"] = True
            if summary_projection:
                round_payload["repair_summary"] = summary_projection
                if round_requires_task_boundary_triage:
                    task_boundary_triage_required = True
                    task_boundary_triage_summary = summary_projection
                    round_payload["task_boundary_triage_required"] = True
            repair_rounds.append(round_payload)
            if round_requires_task_boundary_triage:
                settled_attempt = await _settle_pending_workspace_quality_repair_attempt(
                    executor,
                    pending_round_attempt,
                    accepted=False,
                    reason="workspace_quality_repair_task_boundary_triage_required",
                )
                if settled_attempt is not None:
                    normalized_round_summary["task_runtime_repair_attempt"] = settled_attempt
                convergence_stop_reason = "task_boundary_triage_required"
                break
            if (
                isinstance(summary_projection, dict)
                and str(summary_projection.get("error_code") or "").strip() == "quality_repair_deadline_insufficient"
            ):
                settled_attempt = await _settle_pending_workspace_quality_repair_attempt(
                    executor,
                    pending_round_attempt,
                    accepted=False,
                    reason="workspace_quality_repair_deadline_insufficient",
                )
                if settled_attempt is not None:
                    summary_projection["task_runtime_repair_attempt"] = settled_attempt
                round_payload["verifier_effect"] = "deadline_insufficient"
                round_payload["verifier_authoritative_success"] = False
                convergence_stop_reason = "quality_repair_deadline_insufficient"
                break
            if not round_write_tool_evidence:
                # A provider/tool result is attempt evidence, not delivery
                # progress.  In r51 an ``edit_file`` turn produced no physical
                # mutation, but the non-empty result list still caused
                # ``go test`` and ``go run`` to execute again.  The failed
                # attempt was then reopened for the next round, projecting an
                # active TaskRuntime row with an older failed settlement.
                #
                # Do not spend verifier budget until an authoritative effect
                # receipt proves a write.  Give the same Director task one
                # more edit opportunity, then stop after two consecutive
                # no-mutation rounds.  PM/CE are never restarted here.
                round_payload["verifier_effect"] = "no_op"
                round_payload["verifier_authoritative_success"] = False
                round_payload["diagnostic_count_before"] = len(before_signature)
                round_payload["diagnostic_count_after"] = len(before_signature)
                round_payload["residual_errors_after"] = repair_errors[:10]
                projected_summary_raw = round_payload.get("repair_summary")
                if isinstance(projected_summary_raw, dict):
                    projected_summary: dict[str, Any] = projected_summary_raw
                    projected_summary["claimed_success_before_revalidation"] = bool(projected_summary.get("success"))
                    projected_summary["success"] = False
                    projected_summary["success_authority"] = "post_repair_verifier"
                    projected_summary["verifier_effect"] = "no_op"
                round_error_code = (
                    str(projected_summary_raw.get("error_code") or "").strip()
                    if isinstance(projected_summary_raw, dict)
                    else ""
                )
                if not round_error_code and isinstance(projected_summary_raw, dict):
                    round_error_folded = str(projected_summary_raw.get("error") or "").casefold()
                    if (
                        "request timeout" in round_error_folded
                        or "provider_stream_timeout" in round_error_folded
                        or "llm_timeout" in round_error_folded
                    ):
                        round_error_code = "quality_repair_provider_timeout"
                        projected_summary_raw["error_code"] = round_error_code
                if round_error_code == "quality_repair_provider_timeout":
                    # A provider transport timeout has no semantic repair
                    # effect. Live L3-22 reached five real edits, then a 300s
                    # timeout was counted as the third semantic non-progress
                    # round and tripped the global fuse. Preserve the existing
                    # diagnostic budget and allow exactly one same-owner
                    # transport retry; a second timeout stops explicitly.
                    round_payload["verifier_effect"] = "provider_timeout"
                    if isinstance(projected_summary_raw, dict):
                        projected_summary_raw["verifier_effect"] = "provider_timeout"
                    settled_attempt = await _settle_pending_workspace_quality_repair_attempt(
                        executor,
                        pending_round_attempt,
                        accepted=False,
                        reason="workspace_quality_repair_provider_timeout",
                    )
                    if settled_attempt is not None and isinstance(projected_summary_raw, dict):
                        projected_summary_raw["task_runtime_repair_attempt"] = settled_attempt
                    if not provider_transport_retry_granted:
                        provider_transport_retry_granted = True
                        provider_transport_retry_pending = True
                        round_payload["provider_transport_retry_granted"] = True
                        convergence_stop_reason = "quality_repair_provider_timeout_retry_same_director_task"
                        continue
                    convergence_stop_reason = "quality_repair_provider_timeout_exhausted"
                    break
                settled_attempt = await _settle_pending_workspace_quality_repair_attempt(
                    executor,
                    pending_round_attempt,
                    accepted=False,
                    reason="workspace_quality_repair_no_mutation",
                )
                if settled_attempt is not None and isinstance(projected_summary_raw, dict):
                    projected_summary_raw["task_runtime_repair_attempt"] = settled_attempt
                nonprogress_rounds_since_last_progress += 1
                current_nonprogress_task_id = str(round_summary.get("task_id") or "").strip() or "__unknown_owner__"
                if last_nonprogress_effect == "no_op" and (
                    not current_nonprogress_task_id or current_nonprogress_task_id == last_nonprogress_task_id
                ):
                    consecutive_stagnant_rounds += 1
                else:
                    # Different non-progress classes are different evidence.
                    # A denied/no-effect mutation followed by a real edit that
                    # introduces a compiler diagnostic is not two attempts at
                    # the same failed strategy.  Preserve the cycle breaker,
                    # but allow the same Director task to consume the newly
                    # structured verifier feedback on the next bounded round.
                    consecutive_stagnant_rounds = 1
                    last_nonprogress_effect = "no_op"
                    last_nonprogress_task_id = current_nonprogress_task_id
                claimed_noop_targets = (
                    owned_round_targets
                    or [
                        str(item or "").strip()
                        for item in (round_summary.get("repair_target_files") or [])
                        if str(item or "").strip()
                    ]
                    or list(owner_override[:1] if owner_override else [])
                )
                leftover_after_noop = workspace_quality_unclaimed_residual_targets(
                    repair_errors,
                    claimed_targets=claimed_noop_targets,
                    workspace=Path(executor.workspace),
                )
                # This is the global progress fuse, so it must run BEFORE an
                # owner rotation can ``continue``.  Live L3-21 reached ten
                # no-op rounds (and ~20 TaskRuntime claim/settle transitions):
                # every round found another leftover target, reset the local
                # owner-stagnation counter, and skipped this hard cap.  Owner
                # rotation may change who repairs next, but it is not verified
                # project progress and must not grant an unbounded retry budget.
                if nonprogress_rounds_since_last_progress >= _WORKSPACE_QUALITY_REPAIR_NONPROGRESS_HARD_CAP:
                    convergence_stop_reason = "three_nonprogress_repairs_without_verified_progress"
                    break
                if leftover_targets_should_force_owner_rotate(leftover_after_noop, claimed_noop_targets):
                    # Live L2-15: generator.cpp went syntax-green so the
                    # engine owner no-op'd while ### src/main.cpp still
                    # failed. Do not retry the same Director task.
                    forced_next_owner_targets = leftover_after_noop
                    leftover_extra_pending = True
                    consecutive_stagnant_rounds = 0
                    last_nonprogress_effect = ""
                    last_nonprogress_task_id = ""
                    continue
                if consecutive_stagnant_rounds >= 2:
                    convergence_stop_reason = "two_consecutive_no_mutation_repairs"
                    break
                convergence_stop_reason = "repair_produced_no_effect_retry_same_director_task"
                continue
            latest_check_results = []
            rerun_prepare_results = []
            rerun_results = []
            round_prepare_failed = False
            prepare_phase = "prepare_after_repair" if round_index == 0 else f"prepare_after_repair_{round_index + 1}"
            for command in prepare_commands:
                result, deadline_detail = await run_workspace_quality_command_with_deadline(command, prepare_phase)
                if deadline_detail:
                    await _settle_pending_workspace_quality_repair_attempt(
                        executor,
                        pending_round_attempt,
                        accepted=False,
                        reason="workspace_quality_repair_verifier_deadline_insufficient",
                    )
                    return write_workspace_validation_failure(
                        "factory_quality_gate_workspace_checks_deadline_insufficient",
                        deadline_detail,
                        repair_override=current_workspace_repair_summary(residual_errors=repair_errors),
                    )
                results.append(result)
                rerun_prepare_results.append(result)
                if not bool(result.get("passed")):
                    round_prepare_failed = True
            phase = "check_after_repair" if round_index == 0 else f"check_after_repair_{round_index + 1}"
            if round_prepare_failed:
                for command in run_commands:
                    result = {
                        "command": command,
                        "phase": phase,
                        "exit_code": None,
                        "passed": False,
                        "error": "skipped because workspace validation preparation failed after repair",
                        "stdout_tail": "",
                        "stderr_tail": "",
                    }
                    results.append(result)
                    latest_check_results.append(result)
                    rerun_results.append(result)
            else:
                for command in run_commands:
                    result, deadline_detail = await run_workspace_quality_command_with_deadline(command, phase)
                    if deadline_detail:
                        await _settle_pending_workspace_quality_repair_attempt(
                            executor,
                            pending_round_attempt,
                            accepted=False,
                            reason="workspace_quality_repair_verifier_deadline_insufficient",
                        )
                        return write_workspace_validation_failure(
                            "factory_quality_gate_workspace_checks_deadline_insufficient",
                            deadline_detail,
                            repair_override=current_workspace_repair_summary(residual_errors=repair_errors),
                        )
                    results.append(result)
                    latest_check_results.append(result)
                    rerun_results.append(result)
                round_depth_result = executor._workspace_quality.delivery_depth_contract_result(context)
                if round_depth_result is not None:
                    round_depth_result["phase"] = phase
                    results.append(round_depth_result)
                    latest_check_results.append(round_depth_result)
                    rerun_results.append(round_depth_result)
            round_residual_failures = [item for item in latest_check_results if not bool(item.get("passed"))]
            after_errors = (
                executor._workspace_quality_repair_errors(round_residual_failures) if round_residual_failures else []
            )
            after_signature = executor._workspace_quality_diagnostic_signature(after_errors)
            verifier_passed = not round_residual_failures
            repair_effect = executor._workspace_quality_repair_effect(
                before_signature=before_signature,
                after_signature=after_signature,
                verifier_passed=verifier_passed,
                write_tool_evidence=round_write_tool_evidence,
                before_results=before_check_results,
                after_results=latest_check_results,
            )
            synthesis_verification_round = regression_synthesis_round_pending
            regression_synthesis_round_pending = False
            before_test_identities = _workspace_quality_failing_test_identities(repair_errors)
            current_test_identities = _workspace_quality_failing_test_identities(after_errors)
            if not verifier_passed and repair_effect in {
                "equal_count_swap",
                "forward_unmask",
                "progress",
                "regression",
            }:
                prior_regression_guard_errors = list(regression_guard_errors)
                after_signature_set = set(after_signature)

                def still_current(
                    error: str,
                    *,
                    _current_test_identities: set[str] = current_test_identities,
                    _after_signature_set: set[str] = after_signature_set,
                ) -> bool:
                    identities = _workspace_quality_failing_test_identities([error])
                    if identities and _current_test_identities:
                        return bool(identities & _current_test_identities)
                    return bool(
                        set(executor._workspace_quality_diagnostic_signature([error])).intersection(
                            _after_signature_set
                        )
                    )

                replaced_errors = [
                    error
                    for error in repair_errors
                    if not still_current(error)
                ]
                reintroduced_regression_guard_errors = [
                    error
                    for error in prior_regression_guard_errors
                    if still_current(error)
                ]
                if (
                    replaced_errors
                    and reintroduced_regression_guard_errors
                    and not regression_synthesis_round_granted
                ):
                    # Live L3-22: A -> B -> A can change cardinality (one
                    # gravity test versus two floor tests).  Regression guards
                    # therefore belong to every real diagnostic transition,
                    # not only equal-count swaps.  Grant one synthesis request
                    # containing current A plus prior B.  Do not reset either
                    # stagnation counter: A/B ping-pong cannot renew budget.
                    regression_synthesis_round_granted = True
                    regression_synthesis_round_pending = True
                    round_payload["regression_synthesis_round_granted"] = True
                    round_payload["reintroduced_regression_guard_errors"] = (
                        reintroduced_regression_guard_errors[:6]
                    )
                current_error_set = {str(item or "").strip() for item in after_errors if str(item or "").strip()}
                merged_guards: list[str] = []
                merged_guard_test_identities: set[str] = set()
                for item in [*regression_guard_errors, *replaced_errors]:
                    normalized = str(item or "").strip()
                    identities = _workspace_quality_failing_test_identities([normalized])
                    if (
                        not normalized
                        or normalized in current_error_set
                        or normalized in merged_guards
                        or bool(identities & current_test_identities)
                        or bool(identities & merged_guard_test_identities)
                    ):
                        continue
                    merged_guards.append(normalized[:6000])
                    merged_guard_test_identities.update(identities)
                regression_guard_errors = merged_guards[-6:]
                round_payload["regression_guard_errors_for_next_round"] = regression_guard_errors
                if bool(round_payload.get("regression_synthesis_round_granted")):
                    # Freeze the contract that the synthesis request will
                    # actually receive: current residual A plus newly carried
                    # guard B.  ``reintroduced_regression_guard_errors`` is the
                    # old A guard and cannot represent the A/B union.
                    regression_synthesis_union_test_identities = set(current_test_identities)
                    for guard_error in regression_guard_errors:
                        regression_synthesis_union_test_identities.update(
                            _workspace_quality_failing_test_identities([guard_error])
                        )
            semantic_contract_conflict_this_round = False
            if (
                synthesis_verification_round
                and not verifier_passed
                and regression_synthesis_union_test_identities
                and not current_test_identities < regression_synthesis_union_test_identities
            ):
                owner_task_id = str(round_summary.get("task_id") or "").strip() or "__unknown_owner__"
                semantic_contract_conflict_candidate = {
                    "schema_version": "factory.workspace_quality.semantic_contract_conflict_candidate.v1",
                    "reason": "bounded_regression_synthesis_did_not_reduce_named_test_union",
                    "owner_task_id": owner_task_id,
                    "synthesis_union_test_identities": sorted(regression_synthesis_union_test_identities),
                    "residual_test_identities": sorted(current_test_identities),
                    "pm_ce_restart_allowed": False,
                    "recommended_route": "same_ce_stage_contract_feasibility_review",
                }
                round_payload["semantic_contract_conflict_candidate"] = dict(
                    semantic_contract_conflict_candidate
                )
                semantic_contract_conflict_this_round = True
            elif (
                causal_reanalysis_this_round
                and not verifier_passed
                and before_test_identities
                and current_test_identities
                and not current_test_identities < before_test_identities
                and _workspace_quality_is_pure_named_test_surface(after_signature)
            ):
                # A -> A -> A is the non-oscillating sibling of the A -> B -> A
                # contract-conflict signature above.  After two real edits keep
                # the same named tests red, Factory grants exactly one causal
                # reanalysis request.  If that bounded request still cannot
                # strictly reduce the named-test set, another local edit is not
                # new evidence: route the immutable CE behavior contract for a
                # feasibility review instead of burning the generic hard cap.
                owner_task_id = str(round_summary.get("task_id") or "").strip() or "__unknown_owner__"
                semantic_contract_conflict_candidate = {
                    "schema_version": "factory.workspace_quality.semantic_contract_conflict_candidate.v1",
                    "reason": "bounded_causal_reanalysis_did_not_reduce_named_test_set",
                    "owner_task_id": owner_task_id,
                    "synthesis_union_test_identities": sorted(before_test_identities),
                    "residual_test_identities": sorted(current_test_identities),
                    "pm_ce_restart_allowed": False,
                    "recommended_route": "same_ce_stage_contract_feasibility_review",
                }
                round_payload["semantic_contract_conflict_candidate"] = dict(
                    semantic_contract_conflict_candidate
                )
                semantic_contract_conflict_this_round = True
            settled_attempt = await _settle_pending_workspace_quality_repair_attempt(
                executor,
                pending_round_attempt,
                accepted=repair_effect in {"resolved", "progress"},
                reason=f"workspace_quality_repair_{repair_effect}",
            )
            round_payload.update(
                {
                    "verifier_effect": repair_effect,
                    "verifier_authoritative_success": verifier_passed,
                    "diagnostic_count_before": len(before_signature),
                    "diagnostic_count_after": len(after_signature),
                    "residual_errors_after": after_errors[:10],
                }
            )
            projected_summary_raw = round_payload.get("repair_summary")
            if isinstance(projected_summary_raw, dict):
                projected_summary = projected_summary_raw
                projected_summary["claimed_success_before_revalidation"] = bool(projected_summary.get("success"))
                projected_summary["success"] = verifier_passed
                projected_summary["success_authority"] = "post_repair_verifier"
                projected_summary["verifier_effect"] = repair_effect
                if settled_attempt is not None:
                    projected_summary["task_runtime_repair_attempt"] = settled_attempt
            after_codes = executor._workspace_quality_diagnostic_error_codes(after_signature)
            after_plan_probe = executor._workspace_quality_repair_plan_probe_report(after_errors)
            before_plannable_tools = _workspace_quality_plannable_source_tools(round_plan_probe)
            after_plannable_tools = _workspace_quality_plannable_source_tools(after_plan_probe)
            # A forward_unmask onto error codes already observed earlier in
            # this loop (A -> B -> A ping-pong, or a slide back to a code a
            # prior round already resolved) is oscillation, not phase
            # advancement; it must keep feeding the stagnation breaker.
            forward_unmask_advances = repair_effect == "forward_unmask" and not (
                after_codes & seen_diagnostic_error_codes
            )
            newly_plannable_source_tools = sorted(
                after_plannable_tools - before_plannable_tools - seen_plannable_source_tools
            )
            # Some compilers do not expose stable diagnostic codes for every
            # phase. Go's ``cannot convert`` -> ``undefined: math`` live L3-22
            # transition is one example: the error count stayed equal and the
            # generic code extractor returned an empty set, but the second
            # verifier result newly exposed an executable deterministic import
            # repair. Stopping after that round discarded a concrete next
            # action. Grant exactly one bounded continuation for a newly
            # plannable source_tool; repeating the same tool remains stagnant
            # and still trips the existing cycle breaker/hard cap.
            plannable_repair_unmasked = repair_effect == "equal_count_swap" and bool(
                newly_plannable_source_tools
            )
            if plannable_repair_unmasked:
                round_payload["newly_plannable_source_tools"] = newly_plannable_source_tools
            round_task_id = ""
            projected_for_task = round_payload.get("repair_summary")
            if isinstance(projected_for_task, Mapping):
                round_task_id = str(projected_for_task.get("task_id") or "").strip()
            if not round_task_id:
                round_task_id = "__unknown_owner__"
            if repair_effect in {"resolved", "progress"} or forward_unmask_advances or plannable_repair_unmasked:
                consecutive_stagnant_rounds = 0
                nonprogress_rounds_since_last_progress = 0
                last_nonprogress_effect = ""
                last_nonprogress_task_id = ""
            elif (
                repair_effect == last_nonprogress_effect and round_task_id and round_task_id == last_nonprogress_task_id
            ):
                # Live L1-06: poem.hpp swap then moon.cpp swap are different
                # owners. Counting them as one breaker trip aborted a still
                # advancing multi-task residual.
                consecutive_stagnant_rounds += 1
            else:
                consecutive_stagnant_rounds = 1
                last_nonprogress_effect = repair_effect
                last_nonprogress_task_id = round_task_id
            if (
                repair_effect not in {"resolved", "progress"}
                and not forward_unmask_advances
                and not plannable_repair_unmasked
            ):
                nonprogress_rounds_since_last_progress += 1
            seen_diagnostic_error_codes.update(after_codes)
            seen_plannable_source_tools.update(after_plannable_tools)
            if verifier_passed:
                convergence_stop_reason = "verifier_passed"
                break
            if round_prepare_failed:
                convergence_stop_reason = "prepare_after_repair_failed"
                break
            if semantic_contract_conflict_this_round:
                convergence_stop_reason = "named_test_semantic_contract_conflict_candidate"
                break
            if regression_synthesis_round_pending:
                # One same-Director continuation only. Factory neither writes
                # target files nor restarts PM/CE; the next normal round
                # consumes the now-complete current+guard diagnostic context.
                continue
            if (
                repair_effect == "stagnant"
                and consecutive_stagnant_rounds >= 2
                and not causal_reanalysis_round_granted
                and _workspace_quality_is_pure_named_test_surface(after_signature)
            ):
                # Live L3-22: two authoritative engine.go edits changed file
                # hashes but the exact same named Go tests remained red.  That
                # is evidence the edited branch did not participate in the
                # failing execution path, not permission to repeat the same
                # local patch.  Grant one same-owner causal reanalysis round;
                # keep the global non-progress count so a third stagnant edit
                # still terminates at the existing hard cap.
                causal_reanalysis_round_granted = True
                causal_reanalysis_round_pending = True
                round_payload["causal_reanalysis_round_granted"] = True
                continue
            # Global fuse must precede every owner-rotation ``continue``.
            # A real edit with no verifier reduction is still non-progress.
            # Live L3-21 repeatedly produced equal-count swaps, discovered a
            # leftover path, reset the owner-local counter, and bypassed the
            # only hard cap below.  One QA retry consumed eight rounds / sixteen
            # TaskRuntime claims without reducing the same five test failures.
            if nonprogress_rounds_since_last_progress >= _WORKSPACE_QUALITY_REPAIR_NONPROGRESS_HARD_CAP:
                convergence_stop_reason = "three_nonprogress_repairs_without_verified_progress"
                break
            claimed_round_targets = (
                owned_round_targets
                or [
                    str(item or "").strip()
                    for item in (round_summary.get("repair_target_files") or [])
                    if str(item or "").strip()
                ]
                or list(owner_override[:1] if owner_override else [])
            )
            leftover_tus = workspace_quality_unclaimed_failing_tu_targets(
                after_errors,
                claimed_targets=claimed_round_targets,
                workspace=Path(executor.workspace),
            )
            if leftover_targets_should_force_owner_rotate(leftover_tus, claimed_round_targets):
                # Live L2-15 remint-9: models kept mutating queue.hpp/.cpp
                # (classified progress / forward_unmask) while ### src/main.cpp
                # stayed red. Waiting for two same-owner stagnations never
                # leased the failing TU. Any unsuccessful round with unclaimed
                # ### TUs must rotate immediately.
                forced_next_owner_targets = leftover_tus
                leftover_extra_pending = True
                consecutive_stagnant_rounds = 0
                last_nonprogress_effect = ""
                last_nonprogress_task_id = ""
                continue
            if consecutive_stagnant_rounds >= 2:
                leftover_owners = workspace_quality_unclaimed_residual_targets(
                    after_errors,
                    claimed_targets=claimed_round_targets,
                    workspace=Path(executor.workspace),
                )
                if leftover_targets_should_force_owner_rotate(leftover_owners, claimed_round_targets):
                    forced_next_owner_targets = leftover_owners
                    leftover_extra_pending = True
                    consecutive_stagnant_rounds = 0
                    last_nonprogress_effect = ""
                    last_nonprogress_task_id = ""
                    continue
                if leftover_tus:
                    # Live L2-16 remint-6: Plant.java/Season.java existed,
                    # leftover_tus stayed on claimed PlantEngine.java
                    # (MelodyModel.Note). Aborting at 2 stagnant wasted
                    # remaining javac rounds on the same owner.
                    continue
                convergence_stop_reason = "two_consecutive_stagnant_repairs"
                break
        residual_failures = [item for item in latest_check_results if not bool(item.get("passed"))]
        residual_errors = executor._workspace_quality_repair_errors(residual_failures) if residual_failures else []
        residual_coverage_report = executor._workspace_quality_repair_coverage_report(residual_errors)
        repair_revalidated = bool(rerun_results)
        repair_summary = {
            "attempted": bool(repair_rounds),
            "success": repair_revalidated and not residual_failures,
            "revalidated": repair_revalidated,
            "residual_error_count": len(residual_failures),
            "residual_errors": residual_errors[:10],
            "director_runtime_repair_coverage": residual_coverage_report,
            "plan_probe_preaudit": executor._workspace_quality_repair_plan_probe_report(residual_errors),
            "source_tools": list(dict.fromkeys(source_tools)),
            "tool_results": len(repair_results),
            "write_tool_evidence": write_tool_evidence,
            "artifact_quality_errors": repair_errors[:10],
            "evidence": evidence[:12],
            "max_rounds": max_rounds,
            "rounds": repair_rounds,
            "consecutive_stagnant_rounds": consecutive_stagnant_rounds,
            "nonprogress_rounds_since_last_progress": nonprogress_rounds_since_last_progress,
            "convergence_stop_reason": convergence_stop_reason,
            "provider_transport_retry_granted": provider_transport_retry_granted,
        }
        if semantic_contract_conflict_candidate:
            repair_summary["semantic_contract_conflict_candidate"] = dict(
                semantic_contract_conflict_candidate
            )
        scope_filter = workspace_quality_latest_task_boundary_scope_filter(repair_summary)
        if scope_filter:
            repair_summary["task_boundary_scope_filter"] = scope_filter
        if task_boundary_triage_required:
            repair_summary.update(
                {
                    "task_boundary_triage_required": True,
                    "success_reason": "task_boundary_interface_discrepancy_required",
                    "plan_probe_preaudit": task_boundary_triage_summary.get("plan_probe_preaudit"),
                    "interface_discrepancy_evidence": task_boundary_triage_summary.get(
                        "interface_discrepancy_evidence"
                    ),
                }
            )

    effective_results = rerun_results if rerun_results else results
    if rerun_results:
        effective_results = rerun_prepare_results + rerun_results

    payload_warnings = []
    if bool(repair_summary.get("task_boundary_triage_required")):
        payload_warnings.append("task_boundary_interface_discrepancy_required")

    payload = {
        "schema_version": "factory.workspace_quality_checks.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "factory_stage_executor",
        "factory_run_id": run.id,
        "workspace": str(executor.workspace),
        "passed": all(bool(item.get("passed")) for item in effective_results),
        "commands": results,
        # Preserve every physical attempt above for audit, but project only the
        # terminal verifier epoch into Run Ledger outcome authority.  A failed
        # pre-repair command is immutable history; once the same verifier is
        # rerun successfully it must not keep the repaired delivery red.
        "effective_commands": effective_results,
        "repair": repair_summary,
    }
    if payload_warnings:
        payload["warnings"] = payload_warnings
    artifact = executor._write_workspace_validation_artifact(run, context, payload)
    return bool(payload["passed"]), artifact
