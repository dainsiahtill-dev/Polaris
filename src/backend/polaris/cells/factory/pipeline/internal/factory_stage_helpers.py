"""Pure helper functions for the factory stage executor.

These are the side-effect-free building blocks extracted verbatim from
``OrchestrationStageExecutor`` (text shaping, delivery-target normalization,
director-evidence truth tables, env/bool resolution, command resolution, and
output trimming). ``OrchestrationStageExecutor`` keeps same-named delegating
shims so every existing test-called / subclassed entry point is preserved.

Monkeypatch note: ``resolve_workspace_quality_command`` references ``shutil``
and ``os`` through the module namespace at call time. The historical tests
monkeypatch ``factory_run_service.shutil.which`` / ``factory_run_service.os.name``;
because Python caches module objects, those patches mutate the shared ``shutil``
/ ``os`` module objects this module also imports, so resolution stays patchable.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .factory_run_models import (
    _PM_DIRECTIVE_META_LINE_PATTERN,
    _PM_PLAN_META_DIAGNOSTIC_MARKERS,
    _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS,
)

_DECLARED_FILE_TOKEN_RE = re.compile(
    r"(?<![\w./-])"
    r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\."
    r"(?:py|txt|toml|json|md|html|js|ts|tsx|jsx|css|yaml|yml|go|mod|sum|sh)"
    r"(?![\w.-])"
)
_FILE_AS_DIRECTORY_SUFFIXES = frozenset(
    {
        ".css",
        ".go",
        ".html",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".mod",
        ".py",
        ".sh",
        ".sum",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)
_MAX_DECLARED_DELIVERY_TARGET_CHARS = 240
_PATHLIKE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./@+-]+$")


@dataclass(frozen=True, slots=True)
class CanonicalFactoryAuthority:
    """Read-only authority projected from the canonical Run Ledger.

    Factory stages may use this value to authorize state transitions. Session
    status, report files, workspace contents, and free-form metadata are not
    represented because they are diagnostic projections rather than execution
    facts.
    """

    source_valid: bool
    task_runtime_projection_authoritative: bool
    contract_task_scope_valid: bool
    task_runtime_converged: bool
    terminal_runtime_delivery_recovered: bool
    task_boundary_present: bool
    task_boundary_completed_verified: bool
    qa_verdict_present: bool
    qa_verdict_passed: bool
    sequence_barrier_satisfied: bool
    evidence_policy_passed: bool
    projection_passed: bool
    reason_code: str
    detail: str
    failure_class: str
    responsible_layer: str
    task_count: int
    expected_task_ids: tuple[str, ...]
    missing_runtime_task_ids: tuple[str, ...]
    unexpected_runtime_task_ids: tuple[str, ...]
    incomplete_task_ids: tuple[str, ...]
    incomplete_runtime_task_ids: tuple[str, ...]
    missing_task_boundary_ids: tuple[str, ...]
    recovered_runtime_task_ids: tuple[str, ...]

    @property
    def director_stage_authorized(self) -> bool:
        """Return whether all owned tasks reached ``completed_verified``."""

        runtime_delivery_authorized = self.task_runtime_converged or self.terminal_runtime_delivery_recovered
        return (
            self.source_valid
            and self.task_runtime_projection_authoritative
            and self.contract_task_scope_valid
            and runtime_delivery_authorized
            and self.task_boundary_present
            and self.task_boundary_completed_verified
        )

    @property
    def quality_stage_authorized(self) -> bool:
        """Return whether canonical QA and evidence authorize final success."""

        return (
            self.director_stage_authorized
            and self.qa_verdict_present
            and self.qa_verdict_passed
            and self.sequence_barrier_satisfied
            and self.evidence_policy_passed
            and self.projection_passed
        )


def _alternate_task_id_token(task_id: str) -> str:
    """Map ``TASK-3`` ↔ ``3`` for boundary/runtime id alignment."""

    token = str(task_id or "").strip()
    if not token:
        return ""
    upper = token.upper()
    if upper.startswith("TASK-") and upper[5:].isdigit():
        return upper[5:]
    if token.isdigit():
        return f"TASK-{token}"
    return ""


def _canonical_task_id_token(task_id: Any) -> str:
    """Normalize numeric ``N``/``TASK-N`` aliases to one contract identity."""

    token = str(task_id or "").strip()
    if not token:
        return ""
    upper = token.upper()
    if upper.startswith("TASK-") and upper[5:].isdigit():
        return f"TASK-{int(upper[5:])}"
    if token.isdigit():
        return f"TASK-{int(token)}"
    return token


def _runtime_row_execution_completed(row: Mapping[str, Any]) -> bool:
    """Whether the TaskRuntime lifecycle itself reached completion.

    TaskBoundary owns delivery verification, not execution lifecycle.  A
    ``completed_verified`` boundary therefore cannot rewrite a failed,
    pending, or in-progress TaskRuntime fact into completion.
    """

    state = str(row.get("execution_state") or row.get("status") or "").strip().lower()
    return state == "completed"


def evaluate_canonical_factory_authority(
    projection: Mapping[str, Any] | None,
    *,
    sequence_barrier_satisfied: bool | None = None,
) -> CanonicalFactoryAuthority:
    """Evaluate Factory transition authority from one Run Ledger projection.

    The evaluator is deliberately pure. The Run Ledger cell owns persistence
    and aggregation; Factory only validates the typed projection shape and
    derives a transition decision. The latest verdict per task is used so a
    repaired task supersedes historical failures without erasing audit history.

    Complexity:
        O(t + g + m) time and O(t) memory for ``t`` task verdicts, ``g`` gate
        events, and ``m`` evidence modalities.
    """

    payload = dict(projection or {})
    source_valid = str(payload.get("source") or "").strip() == "run_ledger"

    task_runtime = payload.get("task_runtime_projection")
    task_runtime_map = task_runtime if isinstance(task_runtime, Mapping) else {}
    runtime_readiness = task_runtime_map.get("readiness")
    runtime_readiness_map = runtime_readiness if isinstance(runtime_readiness, Mapping) else {}
    owned_task_ids = task_runtime_map.get("owned_task_ids")
    owned_task_id_items = owned_task_ids if isinstance(owned_task_ids, list) else []
    canonical_owned_items = [_canonical_task_id_token(item) for item in owned_task_id_items]
    expected_task_ids = tuple(sorted({item for item in canonical_owned_items if item}))
    contract_id_collision = len(expected_task_ids) != len(owned_task_id_items)
    contract_task_scope_declared = (
        task_runtime_map.get("owner_scope") == "pm_contract_tasks"
        and bool(expected_task_ids)
        and not contract_id_collision
        and all(canonical_owned_items)
    )

    runtime_rows = task_runtime_map.get("rows")
    runtime_row_items = (
        [row for row in runtime_rows if isinstance(row, Mapping)] if isinstance(runtime_rows, list) else []
    )
    normalized_runtime_rows: dict[str, Mapping[str, Any]] = {}
    runtime_id_collision = False
    for row in runtime_row_items:
        task_id = _canonical_task_id_token(row.get("task_id") or row.get("id"))
        if not task_id:
            continue
        if task_id in normalized_runtime_rows:
            runtime_id_collision = True
            continue
        normalized_runtime_rows[task_id] = row
    observed_runtime_task_ids = set(normalized_runtime_rows)
    expected_runtime_task_ids = set(expected_task_ids)
    missing_runtime_task_ids = tuple(sorted(expected_runtime_task_ids - observed_runtime_task_ids))
    unexpected_runtime_task_ids = tuple(sorted(observed_runtime_task_ids - expected_runtime_task_ids))
    contract_task_scope_valid = (
        contract_task_scope_declared
        and not runtime_id_collision
        and not missing_runtime_task_ids
        and not unexpected_runtime_task_ids
    )
    runtime_rows_authoritative = all(
        row.get("source") == "task_runtime.execution_fact"
        and row.get("status_source") == "task_runtime.execution_fact"
        and isinstance(row.get("fact_event_seq"), int)
        and not isinstance(row.get("fact_event_seq"), bool)
        and int(row.get("fact_event_seq") or 0) >= 1
        for row in normalized_runtime_rows.values()
    )
    task_runtime_projection_authoritative = (
        task_runtime_map.get("schema_version") == "task_runtime.observable_task_rows_authority.v1"
        and task_runtime_map.get("source") == "task_runtime.execution_fact"
        and task_runtime_map.get("authoritative") is True
        and task_runtime_map.get("degraded") is False
        and runtime_readiness_map.get("ready") is True
        and runtime_rows_authoritative
    )
    task_boundary = payload.get("task_boundary")
    task_boundary_map = task_boundary if isinstance(task_boundary, Mapping) else {}
    latest_by_task = task_boundary_map.get("latest_by_task")
    latest_by_task_map = latest_by_task if isinstance(latest_by_task, Mapping) else {}
    # Scope TaskBoundary to the same PM contract identities as TaskRuntime.
    # Auxiliary settlement/verifier boundaries remain observable but cannot
    # add obligations or poison Director delivery authority.
    normalized_verdicts: dict[str, Mapping[str, Any]] = {}
    boundary_id_collision = False
    for raw_task_id, verdict in latest_by_task_map.items():
        task_id = _canonical_task_id_token(raw_task_id)
        if task_id not in expected_runtime_task_ids or not isinstance(verdict, Mapping):
            continue
        if task_id in normalized_verdicts:
            boundary_id_collision = True
            continue
        normalized_verdicts[task_id] = verdict
    contract_task_scope_valid = contract_task_scope_valid and not boundary_id_collision

    incomplete_runtime_task_ids = tuple(
        sorted(task_id for task_id, row in normalized_runtime_rows.items() if not _runtime_row_execution_completed(row))
    )
    task_runtime_converged = (
        task_runtime_projection_authoritative
        and contract_task_scope_valid
        and bool(normalized_runtime_rows)
        and not incomplete_runtime_task_ids
    )
    missing_task_boundary_ids = tuple(
        sorted(
            task_id
            for task_id in expected_task_ids
            if task_id not in normalized_verdicts
        )
    )
    failed_task_boundary_ids = tuple(
        sorted(
            task_id
            for task_id, verdict in normalized_verdicts.items()
            if not (
                bool(verdict.get("ok")) and str(verdict.get("status") or "").strip().lower() == "completed_verified"
            )
        )
    )
    task_boundary_present = bool(normalized_verdicts)
    incomplete_task_ids = tuple(
        sorted(
            set(missing_runtime_task_ids)
            | set(unexpected_runtime_task_ids)
            | set(incomplete_runtime_task_ids)
            | set(missing_task_boundary_ids)
            | set(failed_task_boundary_ids)
        )
    )
    # Both independent axes must converge: every runtime row is completed and
    # every matching TaskBoundary verdict is completed_verified.  TASK-N/N
    # aliases affect identity matching only; neither axis may overwrite the
    # other.
    task_boundary_completed_verified = (
        task_boundary_present
        and not failed_task_boundary_ids
        and not missing_task_boundary_ids
        and task_runtime_converged
    )
    failed_verdict = next(
        (normalized_verdicts[task_id] for task_id in failed_task_boundary_ids if task_id in normalized_verdicts),
        None,
    )
    failure_class = str((failed_verdict or {}).get("failure_class") or "").strip()
    responsible_layer = str((failed_verdict or {}).get("responsible_layer") or "").strip()

    gates = payload.get("gates")
    gate_rows = [item for item in gates if isinstance(item, Mapping)] if isinstance(gates, list) else []
    qa_gate = next(
        (gate for gate in reversed(gate_rows) if str(gate.get("name") or "").strip().lower() == "qa_verdict"),
        None,
    )
    qa_verdict_present = qa_gate is not None
    qa_verdict_passed = bool(qa_gate and qa_gate.get("ok"))
    qa_projection_barrier_satisfied = bool(
        qa_gate and str(qa_gate.get("append_id") or "").strip() and str(qa_gate.get("content_id") or "").strip()
    )
    canonical_sequence_barrier_satisfied = qa_projection_barrier_satisfied
    if sequence_barrier_satisfied is not None:
        canonical_sequence_barrier_satisfied = canonical_sequence_barrier_satisfied and bool(sequence_barrier_satisfied)

    evidence_policy = payload.get("evidence_policy")
    evidence_policy_map = evidence_policy if isinstance(evidence_policy, Mapping) else {}
    missing_required = evidence_policy_map.get("missing_required_modalities")
    failed_required = evidence_policy_map.get("failed_required_modalities")
    evidence_policy_passed = (
        bool(evidence_policy_map.get("integrity_ok"))
        and bool(evidence_policy_map.get("outcome_ok"))
        and not (missing_required if isinstance(missing_required, list) else [])
        and not (failed_required if isinstance(failed_required, list) else [])
    )
    projection_passed = bool(payload.get("integrity_ok")) and bool(payload.get("outcome_ok"))

    reason_code = "canonical_projection_authorized"
    detail = "Canonical Run Ledger projection authorizes Factory completion"
    if not source_valid:
        reason_code = "run_ledger_projection_unavailable"
        detail = "Canonical Run Ledger projection is missing or has a non-canonical source"
    elif not task_runtime_projection_authoritative:
        reason_code = "task_runtime_projection_not_authoritative"
        detail = "TaskRuntime fact-only authority projection is missing or degraded"
        failure_class = "TASK_RUNTIME_PROJECTION_NOT_AUTHORITATIVE"
        responsible_layer = "execution_control_plane"
    elif not contract_task_scope_valid:
        reason_code = "task_runtime_contract_scope_mismatch"
        detail = "PM contract task identities do not exactly match canonical TaskRuntime and TaskBoundary scope"
        failure_class = "EXECUTION_AUTHORITY_SCOPE_MISMATCH"
        responsible_layer = "execution_control_plane"
    elif not normalized_runtime_rows:
        reason_code = "task_runtime_tasks_missing"
        detail = "TaskRuntime authority projection contains no owned task rows"
        failure_class = "EXECUTION_EVIDENCE_MISSING"
        responsible_layer = "execution_control_plane"
    elif not task_boundary_present:
        reason_code = "task_boundary_verdict_missing"
        detail = "No per-task canonical TaskBoundary verdict is available"
    elif failed_task_boundary_ids:
        reason_code = "task_boundary_not_completed_verified"
        detail = "One or more owned tasks did not reach completed_verified"
    elif not task_runtime_converged:
        reason_code = "task_runtime_not_converged"
        detail = "One or more TaskRuntime rows are pending, blocked, active, or failed"
        failure_class = "INCOMPLETE_MATERIALIZATION"
        responsible_layer = "execution_control_plane"
    elif missing_task_boundary_ids or not task_boundary_completed_verified:
        reason_code = "task_boundary_verdict_missing"
        detail = "One or more completed TaskRuntime tasks have no canonical TaskBoundary verdict"
    elif not qa_verdict_present:
        reason_code = "qa_verdict_missing"
        detail = "Canonical Run Ledger projection has no qa_verdict gate"
    elif not canonical_sequence_barrier_satisfied:
        reason_code = "canonical_sequence_barrier_unsatisfied"
        detail = "Canonical qa_verdict append/content coordinates were not observed before quality authorization"
    elif not qa_verdict_passed:
        reason_code = "qa_verdict_failed"
        detail = "Canonical qa_verdict gate failed"
    elif not evidence_policy_passed:
        reason_code = "evidence_policy_failed"
        detail = "Required evidence is missing or failed in the canonical projection"
    elif not projection_passed:
        reason_code = "run_ledger_projection_failed"
        detail = "Canonical Run Ledger integrity or outcome projection failed"

    authority = CanonicalFactoryAuthority(
        source_valid=source_valid,
        task_runtime_projection_authoritative=task_runtime_projection_authoritative,
        contract_task_scope_valid=contract_task_scope_valid,
        task_runtime_converged=task_runtime_converged,
        terminal_runtime_delivery_recovered=False,
        task_boundary_present=task_boundary_present,
        task_boundary_completed_verified=task_boundary_completed_verified,
        qa_verdict_present=qa_verdict_present,
        qa_verdict_passed=qa_verdict_passed,
        sequence_barrier_satisfied=canonical_sequence_barrier_satisfied,
        evidence_policy_passed=evidence_policy_passed,
        projection_passed=projection_passed,
        reason_code=reason_code,
        detail=detail,
        failure_class=failure_class,
        responsible_layer=responsible_layer,
        task_count=len(expected_task_ids),
        expected_task_ids=expected_task_ids,
        missing_runtime_task_ids=missing_runtime_task_ids,
        unexpected_runtime_task_ids=unexpected_runtime_task_ids,
        incomplete_task_ids=incomplete_task_ids,
        incomplete_runtime_task_ids=incomplete_runtime_task_ids,
        missing_task_boundary_ids=missing_task_boundary_ids,
        recovered_runtime_task_ids=(),
    )
    # Canonical completed_verified boundaries carry their own ledger coordinates
    # and evidence. When TaskRuntime is already terminal, the same immutable
    # projection must yield the same Director-to-QA authority in every stage;
    # keeping recovery only in one caller would make QA immediately fail again.
    return recover_terminal_runtime_delivery_authority(payload, authority) or authority


_TERMINAL_DELIVERY_RECOVERY_STATES = frozenset({"failed", "cancelled"})
_TASK_BOUNDARY_BLOCKING_FIELDS = (
    "missing_target_files",
    "missing_entrypoint_targets",
    "unresolved_local_imports",
    "artifact_semantic_mismatches",
    "downstream_pending_artifacts",
    "blocked_dependencies",
    "missing_required_evidence_modalities",
    "failed_required_evidence_modalities",
    "missing_required_verifiers",
    "failed_required_verifiers",
)


def recover_terminal_runtime_delivery_authority(
    projection: Mapping[str, Any] | None,
    authority: CanonicalFactoryAuthority,
) -> CanonicalFactoryAuthority | None:
    """Authorize Director-to-QA handoff after settle without rewriting history.

    TaskRuntime remains the immutable execution-lifecycle record. A terminal
    failed/cancelled row may nevertheless have a later canonical TaskBoundary
    verdict proving every delivery obligation. Factory may use that independent
    delivery fact only after its settle phase. Active, pending, blocked,
    non-authoritative, evidence-free, or failed-boundary rows remain fail-closed.
    """

    if authority.director_stage_authorized:
        return authority
    if (
        not authority.source_valid
        or not authority.task_runtime_projection_authoritative
        or not authority.contract_task_scope_valid
    ):
        return None

    payload = dict(projection or {})
    task_runtime = payload.get("task_runtime_projection")
    task_runtime_map = task_runtime if isinstance(task_runtime, Mapping) else {}
    runtime_rows = task_runtime_map.get("rows")
    runtime_row_items = (
        [row for row in runtime_rows if isinstance(row, Mapping)] if isinstance(runtime_rows, list) else []
    )
    if not runtime_row_items or not authority.expected_task_ids:
        return None

    runtime_rows_by_task: dict[str, Mapping[str, Any]] = {}
    for row in runtime_row_items:
        task_id = _canonical_task_id_token(row.get("task_id") or row.get("id"))
        if not task_id or task_id in runtime_rows_by_task:
            return None
        runtime_rows_by_task[task_id] = row
    if set(runtime_rows_by_task) != set(authority.expected_task_ids):
        return None

    task_boundary = payload.get("task_boundary")
    task_boundary_map = task_boundary if isinstance(task_boundary, Mapping) else {}
    latest_by_task = task_boundary_map.get("latest_by_task")
    latest_by_task_map = latest_by_task if isinstance(latest_by_task, Mapping) else {}
    verdict_by_task: dict[str, Mapping[str, Any]] = {}
    for raw_task_id, verdict in latest_by_task_map.items():
        task_id = _canonical_task_id_token(raw_task_id)
        if task_id not in authority.expected_task_ids or not isinstance(verdict, Mapping):
            continue
        if task_id in verdict_by_task:
            return None
        verdict_by_task[task_id] = verdict
    if set(verdict_by_task) != set(authority.expected_task_ids):
        return None

    recovered_task_ids: list[str] = []
    for task_id in authority.expected_task_ids:
        row = runtime_rows_by_task[task_id]
        state = str(row.get("execution_state") or row.get("status") or "").strip().lower()
        if state != "completed":
            if state not in _TERMINAL_DELIVERY_RECOVERY_STATES:
                return None
            recovered_task_ids.append(task_id)

        verdict = verdict_by_task.get(task_id)
        if not isinstance(verdict, Mapping):
            return None
        if not bool(verdict.get("ok")) or str(verdict.get("status") or "").strip().lower() != "completed_verified":
            return None
        if str(verdict.get("failure_class") or "").strip().upper() != "PASSED":
            return None
        if not str(verdict.get("run_id") or "").strip():
            return None
        if not str(verdict.get("append_id") or "").strip() or not str(verdict.get("content_id") or "").strip():
            return None
        evidence_refs = verdict.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not any(str(item or "").strip() for item in evidence_refs):
            return None
        if any(verdict.get(field_name) for field_name in _TASK_BOUNDARY_BLOCKING_FIELDS):
            return None

    if not recovered_task_ids:
        return None
    recovered_ids = tuple(sorted(set(recovered_task_ids)))
    return replace(
        authority,
        terminal_runtime_delivery_recovered=True,
        task_boundary_completed_verified=True,
        reason_code="terminal_runtime_delivery_recovered",
        detail=(
            "Canonical TaskBoundary delivery evidence authorizes QA after settle; "
            "terminal TaskRuntime failures remain preserved as execution history"
        ),
        failure_class="",
        responsible_layer="execution_control_plane",
        recovered_runtime_task_ids=recovered_ids,
    )


def extend_artifacts(artifacts: list[str], *paths: str) -> None:
    seen = set(artifacts)
    for path in paths:
        normalized = str(path or "").replace("\\", "/").strip().lstrip("/")
        if not normalized or normalized in seen:
            continue
        artifacts.append(normalized)
        seen.add(normalized)


def normalize_declared_delivery_target(value: Any) -> str:
    token = str(value or "").replace("\\", "/").strip().strip("`'\"")
    if (
        not token
        or "\n" in token
        or "\r" in token
        or len(token) > _MAX_DECLARED_DELIVERY_TARGET_CHARS
        or not _PATHLIKE_TOKEN_RE.fullmatch(token)
    ):
        return ""
    while token.startswith("./"):
        token = token[2:]
    token = token.lstrip("/")
    if token.startswith("workspace/"):
        token = token.removeprefix("workspace/")
    if not token or token.endswith("/"):
        return ""
    lowered = token.lower()
    if lowered.startswith(("http://", "https://", "#")):
        return ""
    parts = tuple(part for part in token.split("/") if part)
    if not parts or any(part in {"", ".."} for part in parts):
        return ""
    if any(len(part) > 120 for part in parts):
        return ""
    if parts[0] in {".git", ".polaris", "runtime"}:
        return ""
    for index, part in enumerate(parts[:-1]):
        if Path(part).suffix.lower() in _FILE_AS_DIRECTORY_SUFFIXES:
            return "/".join(parts[: index + 1])
    return token


def collect_declared_delivery_targets(tasks: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    targets: list[str] = []

    def add(value: Any, *, require_file_like: bool = False) -> None:
        normalized = normalize_declared_delivery_target(value)
        if not normalized:
            return
        if require_file_like and not (
            Path(normalized).suffix or normalized.upper().startswith("README") or normalized.startswith("tests/")
        ):
            return
        if normalized in seen:
            return
        targets.append(normalized)
        seen.add(normalized)

    for task in tasks:
        if not isinstance(task, dict):
            continue
        for field in ("target_files", "output_files", "expected_files"):
            raw_values = task.get(field)
            if isinstance(raw_values, str):
                add(raw_values)
            elif isinstance(raw_values, (list, tuple, set)):
                for item in raw_values:
                    add(item)
        raw_scope_paths = task.get("scope_paths")
        if isinstance(raw_scope_paths, str):
            add(raw_scope_paths, require_file_like=True)
        elif isinstance(raw_scope_paths, (list, tuple, set)):
            for item in raw_scope_paths:
                add(item, require_file_like=True)
        scope = str(task.get("scope") or "")
        for item in scope.replace("\n", ",").split(","):
            add(item, require_file_like=True)
        for field in ("goal", "description", "steps", "acceptance", "acceptance_criteria", "execution_checklist"):
            raw_value = task.get(field)
            values = raw_value if isinstance(raw_value, (list, tuple, set)) else [raw_value]
            for value in values:
                text = str(value or "")
                for match in _DECLARED_FILE_TOKEN_RE.finditer(text):
                    add(match.group(0), require_file_like=True)
    return targets


def artifact_file_ready(target: Path) -> bool:
    """Return whether an expected stage artifact is present after upstream completion."""
    try:
        return target.exists() and target.is_file() and target.stat().st_size > 0
    except OSError:
        return False


def is_substantive_doc_text(text: str, *, min_chars: int = 200) -> bool:
    normalized = str(text or "").strip()
    if len(normalized) < min_chars:
        return False
    heading_count = len([line for line in normalized.splitlines() if str(line or "").strip().startswith("#")])
    return heading_count >= 2


def is_pm_meta_diagnostic_task(task: dict[str, Any]) -> bool:
    text = "\n".join(
        str(task.get(key) or "").strip() for key in ("title", "goal", "description") if str(task.get(key) or "").strip()
    ).lower()
    if not text:
        return False
    return any(marker.lower() in text for marker in _PM_PLAN_META_DIAGNOSTIC_MARKERS)


def compact_text_for_prompt(text: str, *, max_chars: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    head_chars = max(max_chars * 2 // 3, 1)
    tail_chars = max(max_chars - head_chars, 1)
    omitted = len(normalized) - head_chars - tail_chars
    return (
        normalized[:head_chars].rstrip()
        + f"\n\n[... omitted {omitted} chars for PM planning context ...]\n\n"
        + normalized[-tail_chars:].lstrip()
    )


def strip_prompt_meta_lines(text: str) -> str:
    lines = [
        line for line in str(text or "").splitlines() if not _PM_DIRECTIVE_META_LINE_PATTERN.search(str(line or ""))
    ]
    return "\n".join(lines).strip()


def build_director_task_filter(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return "Execute ready tasks from PM contract"
    lines: list[str] = []
    for task in tasks[:4]:
        title = str(task.get("title") or task.get("goal") or "").strip()
        scope = str(task.get("scope") or "").strip()
        if not title:
            continue
        if scope:
            lines.append(f"- {title} [scope: {scope}]")
        else:
            lines.append(f"- {title}")
    if not lines:
        return "Execute ready tasks from PM contract"
    return "Execute PM tasks strictly in order:\n" + "\n".join(lines)


def task_string(task: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = task.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is not None and not isinstance(value, (dict, list, tuple, set)):
            token = str(value).strip()
            if token:
                return token
    return ""


def task_string_list(task: dict[str, Any], *keys: str) -> list[str]:
    rows: list[str] = []
    for key in keys:
        value = task.get(key)
        if isinstance(value, list):
            for item in value:
                token = str(item or "").strip()
                if token:
                    rows.append(token)
        elif isinstance(value, str) and value.strip():
            rows.append(value.strip())
    return rows


def is_taskboard_converged(stats: dict[str, int]) -> bool:
    return (
        int(stats.get("pending") or 0) <= 0
        and int(stats.get("ready") or 0) <= 0
        and int(stats.get("in_progress") or 0) <= 0
        and int(stats.get("in_design") or 0) <= 0
        and int(stats.get("in_execution") or 0) <= 0
        and int(stats.get("in_qa") or 0) <= 0
        and int(stats.get("running") or 0) <= 0
        and int(stats.get("processing") or 0) <= 0
        and int(stats.get("executing") or 0) <= 0
        and int(stats.get("waiting_human") or 0) <= 0
        and int(stats.get("blocked") or 0) <= 0
    )


def has_director_progress(before: dict[str, int], after: dict[str, int]) -> bool:
    return any(
        int(after.get(key) or 0) != int(before.get(key) or 0)
        for key in (
            "pending",
            "ready",
            "in_progress",
            "in_design",
            "in_execution",
            "in_qa",
            "running",
            "processing",
            "executing",
            "waiting_human",
            "completed",
            "failed",
            "blocked",
            "cancelled",
            "timeout",
        )
    )


def bool_from_context_or_env(
    context: dict[str, Any],
    *keys: str,
    env_var: str = "",
    default: bool = True,
) -> bool:
    raw: Any = None
    for key in keys:
        if key in context:
            raw = context.get(key)
            break
    if raw is None and env_var:
        raw = os.environ.get(env_var)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    token = str(raw).strip().lower()
    if token in {"1", "true", "yes", "on", "enabled"}:
        return True
    if token in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def trim_command_output(text: str, limit: int = _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS) -> str:
    body = str(text or "")
    if len(body) <= limit:
        return body
    return body[-limit:]


def resolve_workspace_quality_command(command: list[str]) -> list[str]:
    if not command:
        return []
    executable = str(command[0] or "").strip()
    if not executable:
        return []
    resolved = shutil.which(executable)
    if resolved is None and os.name == "nt":
        for suffix in (".cmd", ".exe", ".bat"):
            resolved = shutil.which(f"{executable}{suffix}")
            if resolved:
                break
    if not resolved:
        return []
    return [resolved, *command[1:]]


def qa_report_has_warning(payload: dict[str, Any], warning: str) -> bool:
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        return any(str(item or "").strip() == warning for item in warnings)
    if isinstance(warnings, str):
        return any(part.strip() == warning for part in warnings.split(","))
    return False
