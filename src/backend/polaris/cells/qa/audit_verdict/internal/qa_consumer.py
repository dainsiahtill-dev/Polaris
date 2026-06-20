"""QA consumer that polls PENDING_QA and emits audit verdicts."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from typing import Any

from polaris.cells.qa.audit_verdict.internal.qa_service import QAService
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
)
from polaris.cells.runtime.task_market.public.service import get_task_market_service

logger = logging.getLogger(__name__)
_REQUEUE_STAGE_BY_VERDICT: dict[str, str] = {
    "REQUEUE_EXEC": "pending_exec",
    "RETRY_EXEC": "pending_exec",
    "REQUEUE_DESIGN": "pending_design",
    "RETRY_DESIGN": "pending_design",
    "REQUEUE_QA": "pending_qa",
    "RETRY_QA": "pending_qa",
    "NEEDS_REVIEW": "waiting_human",
    "WAITING_HUMAN": "waiting_human",
    "HITL": "waiting_human",
}
_VALID_ROUTE_STAGES = frozenset({"pending_design", "pending_exec", "pending_qa", "waiting_human"})
_CODE_FILE_EXTENSIONS = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".php",
        ".py",
        ".pyi",
        ".rb",
        ".rs",
        ".scala",
        ".sql",
        ".swift",
        ".ts",
        ".tsx",
    }
)
_CODE_PATH_PREFIXES = ("src/", "test/", "tests/", "app/", "apps/", "backend/", "frontend/", "lib/", "scripts/")
_NO_CHANGE_FLAGS = frozenset(
    {
        "allow_no_changes",
        "no_changes_expected",
        "allow_empty_changed_files",
        "director_noop_allowed",
    }
)
_NO_CHANGE_MODES = frozenset(
    {
        "noop",
        "no_op",
        "no-op",
        "read_only",
        "read-only",
        "inspection",
        "inspection_only",
        "analysis_only",
    }
)
_VERIFIED_EXISTING_SCOPE_MODES = frozenset({"verified_existing_workspace_scope"})

_QA_LLM_AUDIT_ENABLED_ENV = "KERNELONE_QA_LLM_AUDIT_ENABLED"
_QA_LLM_AUDIT_TIMEOUT_ENV = "KERNELONE_QA_LLM_AUDIT_TIMEOUT_SECONDS"
_BOOL_TRUE = {"1", "true", "yes", "on", "enabled"}
_BOOL_FALSE = {"0", "false", "no", "off", "disabled"}


def _read_bool_env(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if value in _BOOL_TRUE:
        return True
    if value in _BOOL_FALSE:
        return False
    return default


def _qa_llm_audit_timeout_seconds() -> float:
    raw = os.environ.get(_QA_LLM_AUDIT_TIMEOUT_ENV, "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return 180.0
        if value > 0:
            return min(value, 900.0)
    return 180.0


def _resolve_qa_route(audit_result: dict[str, Any]) -> tuple[str, str, str]:
    """Resolve QA route from audit result.

    Returns:
        ``(verdict, next_stage, terminal_status)``.
        One of ``next_stage`` / ``terminal_status`` will be non-empty.
    """
    verdict = str(audit_result.get("verdict") or "FAIL").strip().upper() or "FAIL"
    explicit_stage = str(audit_result.get("next_stage") or "").strip().lower()
    if explicit_stage in _VALID_ROUTE_STAGES:
        return verdict, explicit_stage, ""
    if verdict == "PASS":
        return verdict, "", "resolved"
    if verdict in {"FAIL", "REJECT", "REJECTED"}:
        return verdict, "", "rejected"
    mapped_stage = _REQUEUE_STAGE_BY_VERDICT.get(verdict, "")
    if mapped_stage:
        return verdict, mapped_stage, ""
    return verdict, "", "rejected"


# RANK 1 (Reflexion / Actor-Critic): the critic's precise findings must reach the
# actor in a usable form. A content FAIL previously died in a terminal reject (and
# even when requeued, acknowledge_task_stage pops last_failure, which the Director
# is the only reader of) — so the critique was structurally invisible. These helpers
# route bounce-eligible findings through the same last_failure channel the
# deterministic gates already use.
_QA_FINDINGS_REQUEUE_ENV = "KERNELONE_QA_FINDINGS_REQUEUE"
_QA_FINDINGS_REQUEUE_DISABLED = {"off", "none", "disabled", "false", "0"}
_QA_FEEDBACK_MAX_FINDINGS = 5
_QA_FEEDBACK_MAX_CHARS = 600
# A Director success-ack resets the market's per-stage attempt budget, so without a
# cross-stage cap a content FAIL the weak model cannot satisfy would ping-pong
# QA<->Director until lease/wall-clock exhaustion. Bound it with a small per-task cap.
_QA_FINDINGS_MAX_BOUNCES_ENV = "KERNELONE_QA_FINDINGS_MAX_BOUNCES"
_DEFAULT_QA_FINDINGS_MAX_BOUNCES = 2
_QA_FEEDBACK_COUNTERS_KEY = "feedback_counters"
_QA_FINDINGS_COUNTER_KEY = "qa_findings_to_pending_exec"


def _qa_findings_requeue_enabled() -> bool:
    return os.environ.get(_QA_FINDINGS_REQUEUE_ENV, "").strip().lower() not in _QA_FINDINGS_REQUEUE_DISABLED


def _qa_findings_max_bounces() -> int:
    """Resolve the per-task RANK 1 requeue cap (env override, else 2). Clamped to >=0."""
    raw = os.environ.get(_QA_FINDINGS_MAX_BOUNCES_ENV, "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return _DEFAULT_QA_FINDINGS_MAX_BOUNCES
        if value >= 0:
            return value
    return _DEFAULT_QA_FINDINGS_MAX_BOUNCES


def _normalize_feedback_counters(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    counters: dict[str, int] = {}
    for key, value in raw.items():
        name = str(key or "").strip()
        if not name:
            continue
        try:
            count = int(value or 0)
        except (TypeError, ValueError):
            continue
        counters[name] = max(0, count)
    return counters


def _qa_feedback_counters_from_payload(payload: dict[str, Any]) -> dict[str, int]:
    return _normalize_feedback_counters(payload.get(_QA_FEEDBACK_COUNTERS_KEY))


def _qa_findings_are_actionable(findings: Any) -> bool:
    """True iff findings is a non-empty list with at least one non-blank entry."""
    return isinstance(findings, list) and any(str(item).strip() for item in findings)


def _format_qa_findings_feedback(findings: list[Any], verdict: str) -> str:
    """Render QA findings as an actionable, content-preserving repair directive."""
    lines = [str(item).strip() for item in findings if str(item).strip()][:_QA_FEEDBACK_MAX_FINDINGS]
    body = "\n".join(f"- {line}" for line in lines)
    message = (
        f"QA rejected this change ({verdict}). Fix these findings IN PLACE, preserving all "
        f"existing working code (do not rewrite the file from scratch):\n{body}"
    )
    return message[:_QA_FEEDBACK_MAX_CHARS]


def _format_qa_requeue_feedback(audit_result: dict[str, Any], verdict: str) -> str:
    findings = audit_result.get("findings", [])
    if _qa_findings_are_actionable(findings):
        return _format_qa_findings_feedback(findings, verdict)
    metrics = audit_result.get("metrics", {})
    if isinstance(metrics, dict) and metrics.get("missing_director_changed_files_evidence"):
        return (
            "QA requested Director retry: missing_director_changed_files evidence. "
            "Preserve the implementation and publish changed_files/director_changed_files metadata for QA."
        )
    reason = str(audit_result.get("reason") or audit_result.get("summary") or "").strip()
    if reason:
        return f"QA requested Director retry ({verdict}): {reason}"[:_QA_FEEDBACK_MAX_CHARS]
    return f"QA requested Director retry ({verdict})."[:_QA_FEEDBACK_MAX_CHARS]


def _normalize_path_values(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (str, os.PathLike)):
        raw_values: list[Any] = [raw]
    elif isinstance(raw, (list, tuple, set)):
        raw_values = list(raw)
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        if not isinstance(item, (str, os.PathLike)):
            continue
        token = str(item).strip()
        if not token:
            continue
        key = token.replace("\\", "/").lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(token)
    return normalized


def _truthy_payload_flag(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _allows_no_director_changes(payload: dict[str, Any]) -> bool:
    for key in _NO_CHANGE_FLAGS:
        if _truthy_payload_flag(payload, key):
            return True

    for key in ("execution_mode", "task_mode", "mode", "change_mode"):
        mode = str(payload.get(key) or "").strip().lower()
        if mode in _NO_CHANGE_MODES:
            return True
    return False


def _iter_director_evidence_mappings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    mappings = [payload]
    for key in ("director_adapter", "execution_result", "director_result", "execution"):
        nested = payload.get(key)
        if not isinstance(nested, dict):
            continue
        mappings.append(nested)
        adapter_nested = nested.get("adapter_result")
        if isinstance(adapter_nested, dict):
            mappings.append(adapter_nested)
    return mappings


def _has_verified_existing_scope_evidence(payload: dict[str, Any]) -> bool:
    for mapping in _iter_director_evidence_mappings(payload):
        mode = str(mapping.get("materialization_mode") or "").strip().lower()
        if mode not in _VERIFIED_EXISTING_SCOPE_MODES:
            continue
        if mapping.get("success") is False:
            continue
        evidence = mapping.get("existing_contract_evidence")
        if isinstance(evidence, dict) and evidence.get("ok") is True:
            return True
    return False


def _collect_payload_paths(payload: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    paths: list[str] = []
    for key in keys:
        paths.extend(_normalize_path_values(payload.get(key)))
    scope = payload.get("scope")
    if isinstance(scope, list) and "scope" in keys:
        for item in scope:
            if isinstance(item, dict):
                paths.extend(_normalize_path_values(item.get("path") or item.get("file")))
            else:
                paths.extend(_normalize_path_values(item))
    return _normalize_path_values(paths)


def _has_code_path(paths: list[str]) -> bool:
    for raw_path in paths:
        path = raw_path.replace("\\", "/").strip().lower().lstrip("./")
        if not path:
            continue
        if path.endswith(tuple(_CODE_FILE_EXTENSIONS)):
            return True
        if path.startswith(_CODE_PATH_PREFIXES):
            return True
    return False


def _is_code_task_payload(payload: dict[str, Any]) -> bool:
    if _has_code_path(_collect_payload_paths(payload, ("target_files", "scope_paths", "scope"))):
        return True

    task_payload = payload.get("task")
    if isinstance(task_payload, dict) and _has_code_path(
        _collect_payload_paths(task_payload, ("target_files", "scope_paths", "scope"))
    ):
        return True

    text_fields = (
        payload.get("type"),
        payload.get("task_type"),
        payload.get("category"),
        payload.get("title"),
        payload.get("subject"),
        payload.get("goal"),
    )
    haystack = " ".join(str(value).lower() for value in text_fields if value)
    if "document" in haystack or "docs" in haystack:
        return False
    return any(token in haystack for token in ("code", "implement", "fix", "refactor", "test"))


def _requires_director_changed_files(payload: dict[str, Any]) -> bool:
    if _allows_no_director_changes(payload):
        return False
    if _has_verified_existing_scope_evidence(payload):
        return False
    if str(payload.get("blueprint_id") or "").strip():
        return True
    return _is_code_task_payload(payload)


def _extract_director_changed_files(payload: dict[str, Any]) -> list[str]:
    changed_files = _normalize_path_values(payload.get("changed_files"))
    if changed_files:
        return changed_files

    for key in ("director_changed_files", "files_changed"):
        changed_files = _normalize_path_values(payload.get(key))
        if changed_files:
            return changed_files

    for key in ("execution_result", "director_result", "execution"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            changed_files = _normalize_path_values(nested.get("changed_files"))
            if changed_files:
                return changed_files
    return []


def _extract_fallback_audit_files(payload: dict[str, Any]) -> list[str]:
    return _collect_payload_paths(payload, ("target_files", "scope_paths", "scope"))


def _extract_verified_existing_scope_files(payload: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for mapping in _iter_director_evidence_mappings(payload):
        evidence = mapping.get("existing_contract_evidence")
        if isinstance(evidence, dict):
            paths.extend(_normalize_path_values(evidence.get("existing_paths")))
    if paths:
        return _normalize_path_values(paths)
    return _extract_fallback_audit_files(payload)


def _first_failing_verify_clause(verify: str, *, cwd: str) -> str:
    """Clause-level teaching diagnosis — delegates to the KernelOne toolkit
    (single source of truth for the three verify touchpoints)."""
    from polaris.kernelone.quality.step_verify import first_failing_verify_clause

    return first_failing_verify_clause(verify, cwd=cwd)


class QAConsumer:
    """QA consumer for PENDING_QA tasks.

    This consumer polls the task market for tasks in the ``pending_qa`` stage,
    runs the QA audit, and acknowledges the task with ``resolved`` or
    ``rejected`` as the terminal status.

    Args:
        workspace: Workspace path for task market operations.
        worker_id: Unique identifier for this worker instance.
        visibility_timeout_seconds: How long a claimed task is locked before it
            becomes visible to other workers again on failure.
        poll_interval: Seconds to sleep between poll cycles when no task is found.
    """

    def __init__(
        self,
        workspace: str,
        worker_id: str = "qa_worker",
        visibility_timeout_seconds: int = 900,
        poll_interval: float = 5.0,
        enable_llm_audit: bool | None = None,
    ) -> None:
        self._workspace = str(workspace or "").strip()
        if not self._workspace:
            raise ValueError("workspace must be a non-empty string")
        self._worker_id = str(worker_id or "").strip()
        if not self._worker_id:
            raise ValueError("worker_id must be a non-empty string")
        self._visibility_timeout = int(visibility_timeout_seconds)
        self._poll_interval = float(poll_interval)
        self._stop_event = threading.Event()
        self._svc = get_task_market_service()
        self._enable_llm_audit = (
            bool(enable_llm_audit)
            if enable_llm_audit is not None
            else _read_bool_env(_QA_LLM_AUDIT_ENABLED_ENV, default=False)
        )
        # RANK 1 cross-stage bounce bound (I3-r28): per-task count of content-FAIL
        # requeues this run. A Director success-ack resets the market's per-stage
        # attempt budget, so this in-memory cap is what makes an unsatisfiable
        # critique terminal-reject instead of ping-ponging until lease exhaustion.
        self._qa_findings_bounce_counts: dict[str, int] = {}

        # Initialize QA service
        from polaris.cells.qa.audit_verdict.internal.qa_service import QAConfig

        qa_config = QAConfig(workspace=self._workspace, enable_auto_audit=False)
        self._qa_svc = QAService(qa_config)

    def poll_once(self) -> list[dict[str, Any]]:
        """Poll once for PENDING_QA tasks.

        Claims and processes all available tasks until no claimable work remains.
        Returns a list of processed task results, each containing ``task_id``,
        ``ok`` status, and (on failure) ``reason``.
        """
        results: list[dict[str, Any]] = []
        while not self._stop_event.is_set():
            processed = self._claim_and_process_one()
            if processed is None:
                break
            results.append(processed)
        return results

    def run(self) -> None:
        """Run the consumer continuously until stop() is called."""
        logger.info("QA consumer running — press Ctrl+C to stop")
        while not self._stop_event.is_set():
            results = self.poll_once()
            if not results:
                self._stop_event.wait(self._poll_interval)

    def stop(self) -> None:
        """Signal the consumer to stop after the current cycle."""
        self._stop_event.set()

    def _run_step_verify(self, payload: dict[str, Any]) -> str:
        """Run a construction step's machine verify in the workspace.

        Returns '' when there is no step verify or it passes; otherwise a
        teaching failure message for the requeue. The verify command comes
        from the CE blueprint contract (ce-blueprint-tasks/1) and is the
        step's acceptance ground truth by design.
        """
        step = payload.get("construction_step")
        if not isinstance(step, dict):
            return ""
        from polaris.kernelone.quality.step_verify import normalize_step_verify

        verify = normalize_step_verify(step.get("verify"))
        if not verify:
            return ""
        try:
            proc = subprocess.run(
                verify,
                shell=True,
                cwd=self._workspace,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"step verify could not run: {exc} :: {verify!r}"
        if proc.returncode == 0:
            return ""
        output_tail = ((proc.stdout or "") + (proc.stderr or ""))[-400:]
        clause_detail = _first_failing_verify_clause(verify, cwd=self._workspace)
        # The actionable clause goes FIRST: downstream teaching channels
        # truncate (fail_task_stage 600 chars, blueprint step card 240) and a
        # long verify command would push the diagnosis off the visible end.
        if clause_detail:
            return f"step verify failed (exit {proc.returncode}) | {clause_detail} | full: {verify!r} :: {output_tail}".strip()
        return f"step verify failed (exit {proc.returncode}): {verify!r} :: {output_tail}".strip()

    def _run_syntax_gate(self, payload: dict[str, Any]) -> str:
        """Return a precise, weak-model-fixable message when a declared/changed
        source file does NOT parse, else "" (I3-r18 fail-closed backstop).

        Candidates = the step's target_file ∪ Director changed_files ∪ fallback
        audit files, restricted to files that exist on disk. Fail-OPEN when no
        checker could run (the gate blocks only on PROVEN-broken files), so a
        no-node runner never blocks delivery on a file it cannot evaluate.
        """
        from polaris.kernelone.quality import first_syntax_failure
        from polaris.kernelone.quality.artifact_quality import _compress_node_syntax_error

        candidates: list[str] = []
        step = payload.get("construction_step")
        if isinstance(step, dict) and step.get("target_file"):
            candidates.append(str(step["target_file"]))
        candidates.extend(_extract_director_changed_files(payload))
        candidates.extend(_extract_fallback_audit_files(payload))

        seen: set[str] = set()
        existing: list[str] = []
        for raw in candidates:
            rel = str(raw or "").strip()
            if not rel or rel in seen:
                continue
            seen.add(rel)
            if os.path.isfile(os.path.join(self._workspace, rel)):
                existing.append(rel)
        if not existing:
            return ""

        failure = first_syntax_failure(self._workspace, existing)
        if failure is None:
            return ""
        compact = _compress_node_syntax_error(failure.error, failure.path)
        return f"语法检查失败(node --check / py_compile),逐字修正后重试:\n{compact}"

    def _claim_and_process_one(self) -> dict[str, Any] | None:
        """Attempt to claim one PENDING_QA task and process it.

        Returns:
            Processed result dict, or None if no claimable task was found.
        """
        claim = self._svc.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=self._workspace,
                stage="pending_qa",
                worker_id=self._worker_id,
                worker_role="qa",
                visibility_timeout_seconds=self._visibility_timeout,
            )
        )
        if not claim.ok:
            return None

        task_id = str(claim.task_id or "").strip()
        lease_token = str(claim.lease_token or "").strip()

        try:
            payload: dict[str, Any] = dict(claim.payload) if claim.payload else {}

            # Fission steps carry a machine-executable verify — run it FIRST.
            # The generic audit is blind to the step contract (live I3-r9: a
            # step whose verify starts with `test -f ./readme.md` passed QA
            # with score 10 while readme.md did not exist). A verify failure
            # requeues to pending_exec so the Director can correct course.
            verify_failure = self._run_step_verify(payload)
            if verify_failure:
                self._svc.fail_task_stage(
                    FailTaskStageCommandV1(
                        workspace=self._workspace,
                        task_id=task_id,
                        lease_token=lease_token,
                        error_code="QA_step_verify_failed",
                        error_message=verify_failure,
                        requeue_stage="pending_exec",
                    )
                )
                return {
                    "task_id": task_id,
                    "ok": False,
                    "verdict": "FAIL",
                    "reason": "step_verify_failed",
                }

            # I3-r18 fail-closed syntax gate: a grep-based step verify can PASS on
            # a file that does not parse (r18: main.js with a stray ';' inside an
            # object literal satisfied every grep clause but `node --check` failed),
            # shipping a non-running product. Reject a DEFINITELY non-parsing target
            # so the Director repairs it via the corrective re-ask ladder. Fail-OPEN
            # only when no checker could run (node absent / unknown ext / timeout).
            syntax_failure = self._run_syntax_gate(payload)
            if syntax_failure:
                self._svc.fail_task_stage(
                    FailTaskStageCommandV1(
                        workspace=self._workspace,
                        task_id=task_id,
                        lease_token=lease_token,
                        error_code="QA_syntax_failed",
                        error_message=syntax_failure,
                        requeue_stage="pending_exec",
                    )
                )
                return {
                    "task_id": task_id,
                    "ok": False,
                    "verdict": "FAIL",
                    "reason": "syntax_failed",
                }

            # Run QA audit
            audit_result = self._run_qa_audit(task_id, payload)

            verdict, next_stage, terminal_status = _resolve_qa_route(audit_result)

            # RANK 1 (Reflexion/Actor-Critic): a content FAIL with actionable findings
            # must hand them to the Director, not die in a terminal reject. acknowledge_
            # task_stage pops last_failure and the Director only reads payload["last_failure"],
            # so a 'rejected' verdict's findings are structurally invisible. Route the bounce
            # through the same last_failure channel the deterministic gates use (-> pending_exec)
            # so the critique reaches the next attempt. Bounded by the market retry/dead-letter cap.
            audit_findings = audit_result.get("findings", [])
            feedback_counters = _qa_feedback_counters_from_payload(payload)
            persisted_bounce_count = feedback_counters.get(_QA_FINDINGS_COUNTER_KEY, 0)
            local_bounce_count = self._qa_findings_bounce_counts.get(task_id, 0)
            qa_findings_bounce_count = max(persisted_bounce_count, local_bounce_count)
            if (
                terminal_status == "rejected"
                and not next_stage
                and _qa_findings_requeue_enabled()
                and _qa_findings_are_actionable(audit_findings)
                and qa_findings_bounce_count < _qa_findings_max_bounces()
            ):
                next_bounce_count = qa_findings_bounce_count + 1
                self._qa_findings_bounce_counts[task_id] = next_bounce_count
                feedback_counters[_QA_FINDINGS_COUNTER_KEY] = next_bounce_count
                self._svc.fail_task_stage(
                    FailTaskStageCommandV1(
                        workspace=self._workspace,
                        task_id=task_id,
                        lease_token=lease_token,
                        error_code="QA_audit_failed",
                        error_message=_format_qa_findings_feedback(audit_findings, verdict),
                        requeue_stage="pending_exec",
                        metadata={_QA_FEEDBACK_COUNTERS_KEY: feedback_counters},
                    )
                )
                return {
                    "task_id": task_id,
                    "ok": False,
                    "verdict": verdict,
                    "reason": "qa_findings_requeued",
                }

            # Reaching here means this task is terminating or advancing (not a RANK 1
            # requeue): drop its bounce counter so a future task_id reuse starts clean.
            self._qa_findings_bounce_counts.pop(task_id, None)

            if next_stage and next_stage != "waiting_human":
                requeue = self._svc.fail_task_stage(
                    FailTaskStageCommandV1(
                        workspace=self._workspace,
                        task_id=task_id,
                        lease_token=lease_token,
                        error_code=f"QA_{verdict}_requeue",
                        error_message=_format_qa_requeue_feedback(audit_result, verdict),
                        requeue_stage=next_stage,
                        metadata={
                            _QA_FEEDBACK_COUNTERS_KEY: feedback_counters,
                            "qa_next_stage": next_stage,
                            "qa_terminal_status": terminal_status,
                        },
                    )
                )
                return {
                    "task_id": task_id,
                    "ok": bool(requeue.ok),
                    "verdict": verdict,
                    "status": str(requeue.status or ""),
                    "reason": "qa_requeue",
                }

            ack_payload: dict[str, Any] = {
                "verdict": verdict,
                "audit_id": audit_result.get("audit_id", ""),
                "findings": audit_result.get("findings", []),
                "score": audit_result.get("score", 0.0),
                "metrics": audit_result.get("metrics", {}),
                "qa_next_stage": next_stage,
                "qa_terminal_status": terminal_status,
            }
            if feedback_counters:
                ack_payload[_QA_FEEDBACK_COUNTERS_KEY] = feedback_counters

            command_kwargs: dict[str, Any] = {
                "workspace": self._workspace,
                "task_id": task_id,
                "lease_token": lease_token,
                "summary": f"QA verdict: {verdict}",
                "metadata": ack_payload,
            }
            if next_stage:
                command_kwargs["next_stage"] = next_stage
            else:
                command_kwargs["terminal_status"] = terminal_status

            ack = self._svc.acknowledge_task_stage(AcknowledgeTaskStageCommandV1(**command_kwargs))
            return {
                "task_id": task_id,
                "ok": bool(ack.ok),
                "verdict": verdict,
                "status": str(ack.status or ""),
            }

        except Exception as exc:
            logger.exception("QA consumer failed for task %s: %s", task_id, exc)
            self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="QA_audit_failed",
                    error_message=str(exc),
                    requeue_stage="pending_qa",
                )
            )
            return {
                "task_id": task_id,
                "ok": False,
                "reason": str(exc),
            }

    def _build_qa_llm_review_message(
        self,
        *,
        task_id: str,
        task_subject: str,
        changed_files: list[str],
        audit_result: dict[str, Any],
    ) -> str:
        file_sections: list[str] = []
        workspace_root = os.path.abspath(self._workspace)
        for rel_path in changed_files[:4]:
            rel = str(rel_path or "").strip()
            if not rel:
                continue
            abs_path = os.path.abspath(os.path.join(workspace_root, rel))
            try:
                if os.path.commonpath([workspace_root, abs_path]) != workspace_root:
                    continue
            except ValueError:
                continue
            if not os.path.isfile(abs_path):
                continue
            try:
                with open(abs_path, encoding="utf-8") as handle:
                    content = handle.read(2400)
            except (OSError, UnicodeDecodeError):
                continue
            file_sections.append(
                f"FILE {rel}\n----- BEGIN UTF-8 EXCERPT -----\n{content}\n----- END UTF-8 EXCERPT -----"
            )

        return (
            "你是 Polaris QA。请对当前任务产物做一次独立质量审阅。\n"
            "本次审计禁止调用工具；没有工具可用。只输出 JSON 对象，不要 Markdown，不要解释。格式:\n"
            '{"verdict":"PASS|FAIL|NEEDS_REVIEW","findings":["..."],"summary":"..."}\n\n'
            f"task_id: {task_id}\n"
            f"task_subject: {task_subject}\n"
            f"deterministic_audit: {audit_result}\n"
            f"changed_files: {changed_files}\n\n"
            "文件摘录:\n" + ("\n\n".join(file_sections) if file_sections else "(no file excerpts)")
        )

    async def _run_qa_llm_review_async(
        self,
        *,
        task_id: str,
        task_subject: str,
        changed_files: list[str],
        audit_result: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._enable_llm_audit:
            return {"enabled": False}

        run_id = str(payload.get("run_id") or payload.get("source_run_id") or "").strip()
        message = self._build_qa_llm_review_message(
            task_id=task_id,
            task_subject=task_subject,
            changed_files=changed_files,
            audit_result=audit_result,
        )
        try:
            import asyncio

            from polaris.cells.llm.dialogue.public.contracts import InvokeRoleDialogueCommandV1
            from polaris.cells.llm.dialogue.public.service import LlmDialogueService
            from polaris.kernelone.llm.engine.normalizer import ResponseNormalizer

            response = await asyncio.wait_for(
                LlmDialogueService(settings=None).invoke_role_dialogue(
                    InvokeRoleDialogueCommandV1(
                        workspace=self._workspace,
                        role="qa",
                        message=message,
                        context={
                            "run_id": run_id,
                            "task_id": task_id,
                            "domain": "task_market_qa_audit",
                            "disable_internal_tool_rounds": True,
                            "_transaction_kernel_forced_tool_definitions": [],
                            "_transaction_kernel_forced_tool_choice": "none",
                            "llm_max_tokens": 8192,
                        },
                        metadata={
                            "run_id": run_id,
                            "task_id": task_id,
                            "source": "qa.audit_verdict.task_market",
                            "qa_llm_audit": True,
                            "validate_output": False,
                            "max_retries": 0,
                            "prompt_appendix": "No tool calls are available or allowed. Return only the requested JSON object.",
                        },
                    )
                ),
                timeout=_qa_llm_audit_timeout_seconds(),
            )
            content = str(getattr(response, "content", "") or "")
            parsed = ResponseNormalizer.extract_json_object(content) or {}
            verdict = str(parsed.get("verdict") or "").strip().upper()
            raw_findings = parsed.get("findings")
            if isinstance(raw_findings, list):
                findings = [str(item).strip() for item in raw_findings if str(item).strip()]
            elif isinstance(raw_findings, str) and raw_findings.strip():
                findings = [raw_findings.strip()]
            else:
                findings = []
            valid_verdict = verdict in {"PASS", "FAIL", "NEEDS_REVIEW"}
            review_ok = bool(getattr(response, "ok", False)) and bool(parsed) and valid_verdict
            if bool(getattr(response, "ok", False)) and not review_ok:
                findings = [*findings, "QA LLM response did not contain a valid verdict JSON object"]
            return {
                "enabled": True,
                "ok": review_ok,
                "status": str(getattr(response, "status", "") or ""),
                "verdict": verdict,
                "findings": findings[:8],
                "summary": str(parsed.get("summary") or "").strip(),
                "parse_ok": bool(parsed),
                "content_preview": content[:800],
                "metadata": dict(getattr(response, "metadata", {}) or {}),
                "error_code": getattr(response, "error_code", None),
                "error_message": getattr(response, "error_message", None),
            }
        except (ImportError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
            return {
                "enabled": True,
                "ok": False,
                "verdict": "FAIL",
                "findings": [f"QA LLM audit failed: {exc}"],
                "summary": "QA LLM audit failed",
                "parse_ok": False,
                "error_message": str(exc),
            }

    async def _run_qa_audit_async(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run QA audit asynchronously and return result dict.

        Args:
            task_id: Identifier of the task being audited.
            payload: Task payload dict from the task market.

        Returns:
            Audit result dict with ``verdict``, ``audit_id``, ``findings``, ``score``.
        """

        # Extract Director execution evidence from payload.  Ack metadata from
        # Director is merged into task_market payload before QA claims it.
        changed_files: list[str] = []
        require_director_evidence = False
        if isinstance(payload, dict):
            changed_files = _extract_director_changed_files(payload)
            if not changed_files and _has_verified_existing_scope_evidence(payload):
                changed_files = _extract_verified_existing_scope_files(payload)
            require_director_evidence = _requires_director_changed_files(payload)
            if not changed_files and not require_director_evidence:
                changed_files = _extract_fallback_audit_files(payload)

        task_subject = str(payload.get("title", payload.get("subject", task_id)))

        # Run audit
        audit = await self._qa_svc.audit_task(
            task_id=task_id,
            task_subject=task_subject,
            changed_files=changed_files,
            require_changed_files=require_director_evidence,
        )

        # Convert to result dict
        findings = []
        for issue in audit.issues:
            findings.append(
                f"[{issue.get('severity', 'info')}] {issue.get('file', 'unknown')}: {issue.get('message', '')}"
            )

        result: dict[str, Any] = {
            "audit_id": audit.audit_id,
            "verdict": audit.verdict,
            "findings": findings,
            "metrics": dict(audit.metrics),
            "score": audit.metrics.get("files_audited", 0) * 10 if audit.verdict == "PASS" else 0.0,
        }
        if audit.metrics.get("missing_director_changed_files_evidence"):
            result["next_stage"] = "pending_exec"
        llm_review = await self._run_qa_llm_review_async(
            task_id=task_id,
            task_subject=task_subject,
            changed_files=changed_files,
            audit_result=result,
            payload=payload,
        )
        if llm_review.get("enabled"):
            result["llm_review"] = llm_review
            llm_findings = [f"[llm] {item}" for item in llm_review.get("findings", []) if str(item or "").strip()]
            if not bool(llm_review.get("ok", False)):
                result["verdict"] = "FAIL"
                result["score"] = 0.0
                result["findings"] = [*findings, *llm_findings] or ["[llm] QA LLM audit failed"]
            elif str(llm_review.get("verdict") or "").strip().upper() in {"FAIL", "NEEDS_REVIEW"}:
                result["verdict"] = str(llm_review.get("verdict") or "FAIL").strip().upper()
                result["score"] = 0.0
                result["findings"] = [*findings, *llm_findings] or ["[llm] QA LLM requested review"]
        return result

    def _run_qa_audit(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Synchronous wrapper for QA audit.

        Args:
            task_id: Identifier of the task being audited.
            payload: Task payload dict from the task market.

        Returns:
            Audit result dict with ``verdict``, ``audit_id``, ``findings``, ``score``.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(self._run_qa_audit_async(task_id, payload))
