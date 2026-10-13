"""Adapt canonical role-kernel turns to the existing session orchestrator.

This module owns no execution facts: TransactionKernel commits each turn,
roles.session persists its conversation, and the returned receipts are projections.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from collections.abc import AsyncGenerator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from polaris.cells.roles.kernel.public.llm_caller_contracts import (
    clamp_role_execution_timeout_to_deadline,
    prepare_role_session_continuation_policy,
)
from polaris.cells.roles.kernel.public.turn_contracts import TurnContinuationMode
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    ContentChunkEvent,
    ErrorEvent,
    SessionCompletedEvent,
    SessionWaitingHumanEvent,
    TurnEvent,
)
from polaris.cells.roles.profile.public.service import RoleTurnResult
from polaris.cells.roles.runtime.internal.session_orchestrator import RoleSessionOrchestrator
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1, RoleExecutionResultV1
from polaris.kernelone.audit.context_os_prompt import (
    CONTROL_PLANE_PROMPT_KEYS,
    find_control_plane_prompt_content_hits,
    normalize_control_plane_prompt_key,
)
from polaris.kernelone.context.prompt_safety import (
    format_tool_failure_summary,
    parse_tool_failure_summary,
    prompt_safe_tool_failure_summary,
)
from polaris.kernelone.quality import (
    ArtifactQualityEvidence,
    artifact_quality_issue_raw,
    artifact_quality_issue_structural_key,
    artifact_quality_issues_from_errors,
    normalize_file_ownership_target,
    partition_paths_by_declared_scope,
    scan_workspace_artifact_quality_evidence,
)
from polaris.kernelone.quality.interface_ledger import WorkspaceReadSnapshot


def _transcript_data_plane(value: Any) -> Any:
    """Keep tool observations while excluding execution and receipt authority."""
    if isinstance(value, Mapping):
        excluded = {
            "effect_receipt",
            "effect_receipt_commit",
            "director_policy",
            "authorization",
            "execution_envelope",
            "execution_attempt_authority",
            "job_token",
            "job_token_id",
            "job_token_set",
            "job_token_set_hash",
            "claim_grant",
            "claim_grant_hash",
            "capability_token",
        }
        return {str(key): item for key, item in value.items() if str(key) not in excluded}
    return value


def _feedback_text_is_private(value: str) -> bool:
    authority_keys = CONTROL_PLANE_PROMPT_KEYS | {
        "actor_id",
        "call_id",
        "turn_id",
        "fencing_token",
        "receipt_hash",
        "request_hash",
        "execution_authority_hash",
    }
    return bool(find_control_plane_prompt_content_hits(value)) or any(
        normalize_control_plane_prompt_key(key) in authority_keys
        for key in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*['\"]?\s*[:=]", value)
    )


def _failure_argument_observations(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Bound rejected operands; source/code and nested authority stay private."""
    observations: dict[str, Any] = {}
    for key, value in list(arguments.items())[:32]:
        name = str(key)
        if _feedback_text_is_private(name + ":"):
            continue
        if isinstance(value, str):
            bulk = name.lower() in {"content", "text", "body", "source", "code"}
            if not bulk and len(value) <= 1000 and not _feedback_text_is_private(value):
                observations[name] = value
            else:
                observations[name] = {"type": "str", "chars": len(value)}
        elif value is None or isinstance(value, bool | int | float):
            observations[name] = value
        else:
            observations[name] = {
                "type": type(value).__name__,
                **({"items": len(value)} if isinstance(value, Mapping | list | tuple) else {}),
            }
    return observations


def _native_failure_observation(name: str, observation: Mapping[str, Any], arguments: Mapping[str, Any]) -> str:
    """Reuse the ContextOS failure projection for native correlated tool slots."""
    safe = prompt_safe_tool_failure_summary("tool", json.dumps(dict(observation), ensure_ascii=False))
    summary = parse_tool_failure_summary(safe) if safe else None
    if summary is None:
        summary = {"reason": "tool execution failed", "error_type": "tool_failure"}
    for field in ("reason", "error_type", "scope"):
        value = summary.get(field)
        if isinstance(value, str) and _feedback_text_is_private(value):
            if field == "scope":
                summary.pop(field, None)
            else:
                summary[field] = "tool execution failed" if field == "reason" else "tool_failure"
    summary.update(
        tool=name,
        schema_version="tool_failure_summary.v1",
        prompt_safe=True,
        observation_only=True,
        non_deliverable=True,
    )
    payload = observation.get("result")
    payload = payload if isinstance(payload, Mapping) else {}
    rejected = payload.get("arguments")
    summary["arguments"] = _failure_argument_observations(rejected if isinstance(rejected, Mapping) else arguments)
    diagnostics: dict[str, Any] = {}
    for field in ("stderr", "stdout", "exit_code", "returncode", "physical_error", "error_code", "file", "path"):
        value = payload.get(field)
        if isinstance(value, str) and not _feedback_text_is_private(value):
            diagnostics[field] = value[:2400]
        elif isinstance(value, int) and not isinstance(value, bool):
            diagnostics[field] = value
    if diagnostics:
        summary["diagnostics"] = diagnostics
    approved = payload.get("approved_command_scope")
    if isinstance(approved, list):
        rows = []
        for item in approved[:16]:
            if not isinstance(item, Mapping):
                continue
            command, cwd = item.get("command"), item.get("cwd")
            if (
                isinstance(command, str)
                and isinstance(cwd, str)
                and len(command) <= 2000
                and len(cwd) <= 500
                and not _feedback_text_is_private(command + " " + cwd)
            ):
                rows.append({"command": command, "cwd": cwd})
        summary["approved_command_scope"] = rows
        if len(rows) != len(approved):
            summary["omitted_approved_command_count"] = len(approved) - len(rows)
    return str(format_tool_failure_summary(summary))


def _committed_transcript_messages(results: list[RoleTurnResult]) -> list[dict[str, Any]]:
    """Derive native history from the same session's committed kernel results."""
    messages: list[dict[str, Any]] = []
    for result in results:
        commit = result.metadata.get("turn_commit_receipt")
        if not isinstance(commit, Mapping) or not commit.get("turn_id"):
            continue
        calls = {str(item.get("call_id") or ""): item for item in result.tool_calls if isinstance(item, Mapping)}
        native_calls: list[dict[str, Any]] = []
        tool_messages: list[dict[str, Any]] = []
        for row in (result.batch_receipt or {}).get("results", []):
            if not isinstance(row, Mapping):
                continue
            call_id = str(row.get("call_id") or "")
            call = calls.get(call_id)
            name = str(row.get("tool_name") or "")
            if row.get("status") not in {"success", "error", "failed", "blocked", "timeout", "aborted", "cancelled"}:
                continue
            if not call_id:
                continue
            if call is None or call.get("tool") != name or not isinstance(call.get("args"), Mapping):
                raise RuntimeError("committed_tool_transcript_missing_native_arguments:" + call_id)
            native_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(
                            dict(call["args"])
                            if row.get("status") == "success"
                            else _failure_argument_observations(call["args"]),
                            ensure_ascii=False,
                        ),
                    },
                }
            )
            observation = {"status": row.get("status"), "result": _transcript_data_plane(row.get("result"))}
            if row.get("error"):
                observation["error"] = row["error"]
            tool_messages.append(
                {
                    "role": "tool",
                    "name": name,
                    "tool_call_id": call_id,
                    "content": (
                        json.dumps(observation, ensure_ascii=False)
                        if row.get("status") == "success"
                        else _native_failure_observation(name, observation, call["args"])
                    ),
                    "is_error": row.get("status") != "success",
                }
            )
        if native_calls:
            messages.append({"role": "assistant", "content": "", "tool_calls": native_calls})
            messages.extend(tool_messages)
    return messages


def _failed_batch_feedback_summary(batch_receipt: Mapping[str, Any] | None) -> str:
    """Project only failed tool/path/error observations, never receipt authority."""
    rows = (batch_receipt or {}).get("results")
    if not isinstance(rows, list | tuple):
        return ""
    summaries: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("status") not in {
            "error",
            "failed",
            "timeout",
            "aborted",
            "cancelled",
        }:
            continue
        result = row.get("result")
        result = result if isinstance(result, Mapping) else {}
        arguments = row.get("arguments")
        arguments = arguments if isinstance(arguments, Mapping) else {}
        tool = str(row.get("tool_name") or row.get("tool") or "unknown")[:100]
        path = str(arguments.get("file") or result.get("file") or "")[:250]
        error_type = str(
            result.get("physical_error_type")
            or result.get("error_type")
            or result.get("error_code")
            or "tool_result_failed"
        )[:100]
        reason_value = result.get("physical_error") or row.get("error") or result.get("error")
        reason = " ".join(reason_value.split())[:600] if isinstance(reason_value, str) else "tool execution failed"
        summaries.append(
            format_tool_failure_summary(
                {
                    "schema_version": "tool_failure_summary.v1",
                    "tool": tool,
                    "error_type": error_type,
                    "reason": f"{path}: {reason}" if path else reason,
                    "prompt_safe": True,
                    "observation_only": True,
                    "non_deliverable": True,
                }
            )
        )
        if len(summaries) >= 3:
            break
    return "\n".join(summaries)[:1800]


def _current_quality_repair_projection(
    command: ExecuteRoleSessionCommandV1,
    context: dict[str, Any],
    previous: RoleTurnResult | None,
    previous_static_evidence: tuple[ArtifactQualityEvidence, ...] = (),
) -> tuple[dict[str, Any], str | None, ArtifactQualityEvidence | None]:
    """Refresh read-only observations; the task and tool authority never change."""
    quality = context.get("director_quality_repair")
    objective = context.get("task_objective")
    if (
        command.role != "director"
        or not isinstance(quality, Mapping)
        or not isinstance(objective, str)
        or not objective
    ):
        return context, None, None
    raw_paths = quality.get("scan_relative_paths")
    if not isinstance(raw_paths, list | tuple) or not raw_paths:
        return context, None, None
    paths = tuple(dict.fromkeys(str(path) for path in raw_paths))
    if any(Path(path).is_absolute() or ".." in Path(path).parts or path in {"", "."} for path in paths):
        raise RuntimeError("quality_continuation_invalid_read_scope")
    context_metadata = context.get("metadata")
    context_metadata = context_metadata if isinstance(context_metadata, Mapping) else {}
    if "execution_envelope" in context:
        source = context["execution_envelope"]
    elif "control_plane_job_token" in context_metadata:
        source = context_metadata["control_plane_job_token"]
    else:
        raise RuntimeError("quality_continuation_read_scope_authority_missing")
    if not isinstance(source, Mapping):
        raise RuntimeError("quality_continuation_read_scope_authority_malformed")
    declared_reads = source.get("allowed_read_paths")
    if not isinstance(declared_reads, list | tuple) or not all(isinstance(path, str) for path in declared_reads):
        raise RuntimeError("quality_continuation_read_scope_malformed")
    read_scope = list(declared_reads)
    if not read_scope or partition_paths_by_declared_scope(paths, read_scope)[1]:
        raise RuntimeError("quality_continuation_read_scope_mismatch")
    root = Path(command.workspace)

    def inputs() -> dict[str, tuple[str, str]]:
        reader = WorkspaceReadSnapshot(root)
        return {path: (read.status, read.content_sha256) for path in paths for read in (reader.read(root / path),)}

    before = inputs()
    evidence = scan_workspace_artifact_quality_evidence(
        command.workspace, relative_paths=paths, scan_mode="pure_static", task_id=str(command.task_id or "")
    )
    after = inputs()
    if before != after:
        raise RuntimeError("quality_continuation_source_snapshot_changed")
    input_hash = hashlib.sha256(json.dumps(after, sort_keys=True).encode("utf-8")).hexdigest()
    covered = set(evidence.scanned_relative_paths)
    # Script coverage may be unknown while the static import surface is known.
    # Any source/read/ledger uncertainty prevents retiring an old diagnostic.
    static_known = all(status == "readable" for status, _hash in after.values()) and all(
        reason.startswith("script_invocation_unknown:") for reason in evidence.incomplete_reasons
    )
    prior_slots = [
        quality,
        *[context.get(key) for key in ("workspace_quality_evidence", "failed_gate_evidence", "failure_evidence")],
    ]
    old_errors = list(
        dict.fromkeys(
            error
            for slot in prior_slots
            if isinstance(slot, Mapping)
            for field in ("artifact_quality_errors", "quality_errors")
            for error in slot.get(field, ())
            if isinstance(error, str) and error.strip()
        )
    )
    old_issues = artifact_quality_issues_from_errors(old_errors)
    current_keys = {artifact_quality_issue_structural_key(issue.to_dict()) for issue in evidence.issues}

    # Only this bridge's actual prior query object admits generic static
    # predicates. Public context maps/hashes cannot forge query provenance.
    prior_static = {
        artifact_quality_issue_raw(issue): issue.to_dict()
        for query in previous_static_evidence
        if query.scan_mode == "pure_static"
        for issue in query.issues
        if issue.severity == "error" and artifact_quality_issue_raw(issue) in query.errors
    }
    task = context.get("task")
    task = task if isinstance(task, Mapping) else {}
    raw_targets = quality.get("declared_target_files", task.get("target_files", ()))
    targets = [normalize_file_ownership_target(path) for path in raw_targets if isinstance(path, str)]
    missing_identities = {
        f"Artifact quality scan failed: declared target file missing '{path}'": path
        for path in targets
        if path and path in paths
    }
    empty_hash = hashlib.sha256(b"").hexdigest()

    def observed_static_predicate(issue: Mapping[str, Any]) -> bool:
        raw = artifact_quality_issue_raw(issue)
        canonical = artifact_quality_issues_from_errors((raw,))
        if not canonical:
            return False
        # Existence is independent of compiler/parser/sibling coverage.
        owner_target = missing_identities.get(raw)
        if owner_target and canonical[0].get("code") == "declared_target_missing":
            status, digest = after[owner_target]
            return status == "readable" and digest != empty_hash
        admitted = prior_static.get(raw)
        if admitted is not None:
            owner_metadata = admitted.get("metadata")
            if isinstance(owner_metadata, Mapping) and owner_metadata.get("importer_path") and not static_known:
                return False
            path = str(admitted.get("path") or "")
            if not path:
                return evidence.scan_complete and all(status == "readable" for status, _digest in after.values())
            if path not in covered or after.get(path, ("unknown", ""))[0] != "readable":
                return False
            # Skipped/truncated/unreadable input cannot establish a new
            # predicate result. An unrelated missing target does not block it.
            blockers = (
                f"file_checks_skipped:{path}",
                f"file_truncated:{path}",
                f"file_unreadable:{path}",
                f"unsupported_source:{path}",
                f"scope_symlink_unvisited:{path}",
            )
            return not any(
                reason.startswith(blockers) or reason in {"error_limit", "scan_exception"}
                for reason in evidence.incomplete_reasons
            )
        # Preserve the existing public typed import compatibility proof. Other
        # legacy families without real query provenance stay unknown.
        metadata = canonical[0].get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        importer = str(metadata.get("importer_path") or canonical[0].get("path") or "")
        key = artifact_quality_issue_structural_key(canonical[0])
        return bool(
            static_known
            and key
            and key[0] in {"unresolved_import_symbol", "unresolved_relative_import"}
            and importer in covered
            and metadata.get("module", metadata.get("specifier"))
        )

    retained = [
        error for error, issue in zip(old_errors, old_issues, strict=True) if not observed_static_predicate(issue)
    ]
    retired_keys = [
        artifact_quality_issue_structural_key(issue)
        for issue in old_issues
        if observed_static_predicate(issue) and artifact_quality_issue_structural_key(issue) not in current_keys
    ]
    retained_structured = [
        dict(issue)
        for slot in prior_slots
        if isinstance(slot, Mapping)
        for field in ("issues", "artifact_quality_issues")
        for issue in slot.get(field, ())
        if isinstance(issue, Mapping) and not observed_static_predicate(issue)
    ]
    errors = list(dict.fromkeys([*retained, *evidence.errors]))
    missing = [path for path, (status, _hash) in after.items() if status == "missing"]
    errors.extend(f"Missing authorized target/input file: {path}" for path in missing)
    owned_previous_reasons = {reason for query in previous_static_evidence for reason in query.incomplete_reasons}
    resolved_read_reasons = {
        f"{kind}:{path}"
        for path in paths
        if after[path][0] == "readable" and after[path][1] != empty_hash
        for kind in ("scope_missing", "file_unreadable", "file_checks_skipped", "file_truncated")
        if f"{kind}:{path}" in owned_previous_reasons
        and f"{kind}:{path}" not in evidence.incomplete_reasons
        and path in covered
    }
    pending_checks = [check.to_dict() for check in evidence.deferred_checks]
    incomplete_reasons = list(evidence.incomplete_reasons)

    for slot in prior_slots:
        if not isinstance(slot, Mapping):
            continue
        checks = slot.get("deferred_checks")
        if isinstance(checks, list | tuple):
            pending_checks.extend(dict(check) for check in checks if isinstance(check, Mapping))
        reasons = slot.get("incomplete_reasons")
        if isinstance(reasons, list | tuple):
            incomplete_reasons.extend(str(reason) for reason in reasons if str(reason) not in resolved_read_reasons)
    pending_checks = list({json.dumps(check, sort_keys=True): check for check in pending_checks}.values())
    incomplete_reasons = list(dict.fromkeys(incomplete_reasons))
    coverage_complete = (
        evidence.checks_complete
        and not pending_checks
        and not incomplete_reasons
        and all(slot.get("checks_complete") is not False for slot in prior_slots if isinstance(slot, Mapping))
    )
    all_issues = [*artifact_quality_issues_from_errors(errors), *retained_structured]
    all_issues = list({json.dumps(issue, sort_keys=True): issue for issue in all_issues}.values())
    refreshed = {
        **dict(quality),
        "artifact_quality_errors": errors,
        "artifact_quality_issues": all_issues,
        "current_static_evidence": evidence.to_dict(),
        "input_hash": input_hash,
        "retired_static_issue_keys": retired_keys,
        "missing_target_files": missing,
    }
    result = dict(context)
    result["director_quality_repair"] = refreshed
    # Replace mutable projections rather than keeping the first failed scan as
    # current truth. Original failures remain in the committed private history.
    for key in ("workspace_quality_evidence", "failed_gate_evidence", "failure_evidence"):
        original = context.get(key)
        result[key] = {
            **(dict(original) if isinstance(original, Mapping) else {}),
            "artifact_quality_errors": errors,
            "quality_errors": errors,
            "missing_target_files": missing,
            "issues": refreshed["artifact_quality_issues"],
            "deferred_checks": pending_checks,
            "incomplete_reasons": incomplete_reasons,
            "checks_complete": coverage_complete,
        }
        if key in refreshed:
            refreshed[key] = dict(result[key])
    safe_errors = [error for error in errors if not _feedback_text_is_private(error)]
    feedback = {
        "errors": safe_errors,
        "issues": [
            {
                key: value
                for key, value in issue.items()
                if key in {"code", "message", "path", "severity", "source", "line", "column"}
                and not (isinstance(value, str) and _feedback_text_is_private(value))
            }
            for issue in retained_structured
        ],
        "deferred_checks": [
            {
                key: value
                for key, value in check.items()
                if key
                in {
                    "check_id",
                    "relative_paths",
                    "reason",
                    "script_name",
                    "project_relative_path",
                    "required",
                    "status",
                    "command",
                    "cwd",
                }
                and not (isinstance(value, str) and _feedback_text_is_private(value))
            }
            for check in pending_checks
        ],
        "incomplete_reasons": [reason for reason in incomplete_reasons if not _feedback_text_is_private(reason)],
        "checks_complete": coverage_complete,
    }
    commands = [*context.get("allowed_commands", ()), *context.get("verification_commands", ())]
    commands = list(dict.fromkeys(value for value in commands if isinstance(value, str)))
    approved = [str(value) for value in commands if isinstance(value, str) and not _feedback_text_is_private(value)]
    failure = _failed_batch_feedback_summary(previous.batch_receipt) if previous else ""
    if previous:
        observations = []
        for row in (previous.batch_receipt or {}).get("results", []):
            if isinstance(row, Mapping) and row.get("status") in {"error", "failed", "timeout", "aborted", "cancelled"}:
                args = row.get("arguments")
                observations.append(
                    _native_failure_observation(
                        str(row.get("tool_name") or "tool"), row, args if isinstance(args, Mapping) else {}
                    )
                )
        failure = "\n".join(observations) or failure
    targets = [str(path) for path in context.get("target_files", ()) if isinstance(path, str)]
    prompt = (
        f"{objective}\n\nCURRENT QUALITY OBSERVATIONS (read-only; no completion verdict):\n"
        f"{json.dumps(feedback, ensure_ascii=False)}\n"
        f"Authorized task outputs (existing scope): {json.dumps(targets, ensure_ascii=False)}\n"
        "Repair only current failures within the existing authorized task scope. Read current UTF-8 content before "
        "an exact edit; copy SEARCH literally. Create missing authorized outputs without reading nonexistent files. "
        "Preserve unrelated content. When static residuals are empty, execute the approved environment preparation "
        "and remaining build/test/lint/product-entry verification; deferred or unknown checks are still required. "
        "Already materialized outputs do not require another write on every turn. Repair actual failed verification "
        "and rerun it; never declare success from an empty static error list.\n"
        f"Approved verification recipes: {json.dumps(approved, ensure_ascii=False)}\n"
        f"Current failed tool observations: {failure}"
    )
    return result, prompt, evidence


def kernel_session_continuation_enabled(command: ExecuteRoleSessionCommandV1) -> bool:
    policy = command.context.get("continuous_execution")
    if isinstance(policy, Mapping) and "enabled" in policy:
        return policy["enabled"] is True
    return (
        command.role == "director"
        and command.host_kind == "director_adapter"
        and bool(command.task_id)
        and str(command.domain or "code").lower() == "code"
    )


class KernelSessionTurnBridge:
    """One canonical kernel invocation per orchestrator turn; never dispatch tools."""

    canonical_role_kernel = True

    def __init__(self, runtime: Any, command: ExecuteRoleSessionCommandV1) -> None:
        self.runtime = runtime
        self.command = command
        self.context = dict(command.context)
        self.context["continuous_execution"] = {**dict(self.context.get("continuous_execution") or {}), "enabled": True}
        self.results: list[RoleTurnResult] = []
        self.first_error: str | None = None
        self._static_quality_evidence: tuple[ArtifactQualityEvidence, ...] = ()
        enclosing = [
            float(value)
            for key in ("request_timeout_seconds", "timeout_seconds")
            if isinstance(value := command.context.get(key), int | float)
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value > 0
        ]
        # The command timeout describes a physical provider call. Prefer the
        # existing TaskRuntime budget for the entire continuous session.
        # Match the Director owner's enclosing watchdog semantics; a narrower
        # physical projection in one legacy timeout field cannot truncate it.
        budget = max(enclosing) if enclosing else command.timeout_seconds
        if budget is not None:
            budget = min(
                budget,
                clamp_role_execution_timeout_to_deadline(
                    timeout_seconds=max(1, math.ceil(budget)), context=self.context
                ),
            )
        self.deadline = time.monotonic() + budget if budget is not None else None

    def _turn_command(self, turn_id: str, message: str) -> ExecuteRoleSessionCommandV1:
        metadata = dict(self.command.metadata)
        # Invocation locators are deterministic per turn. Execution scope and
        # TaskRuntime authority remain untouched and are revalidated below.
        for key in (
            "transaction_invocation_identity",
            "transaction_attempt_identity",
            "transaction_attempt",
            "transaction_attempt_id",
            "turn_transition_id",
        ):
            metadata.pop(key, None)
        seed = "\0".join(
            (
                str(self.command.turn_request_id),
                self.command.workspace,
                str(self.command.run_id),
                str(self.command.task_id),
                self.command.session_id,
                turn_id,
            )
        )
        metadata["transaction_invocation_id"] = "session-turn-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()
        metadata["continuous_execution_turn_id"] = turn_id
        metadata["role_session_id"] = self.command.session_id
        context, metadata = prepare_role_session_continuation_policy(
            dict(self.context),
            metadata,
            role=self.command.role,
            host_kind=self.command.host_kind,
            previous_result=self.results[-1] if self.results else None,
            prior_results=tuple(self.results),
        )
        context, quality_prompt, static_evidence = _current_quality_repair_projection(
            self.command, context, self.results[-1] if self.results else None, self._static_quality_evidence
        )
        if static_evidence is not None:
            self._static_quality_evidence = (*self._static_quality_evidence, static_evidence)
        if quality_prompt is not None:
            message = quality_prompt
        # The kernel assembly reconstructs current authority messages. The
        # session's prior native observations have one producer below.
        context.pop("_transaction_kernel_prebuilt_messages", None)
        if context.get("_roles_kernel_committed_session_continuation") is TurnContinuationMode.AUTO_CONTINUE:
            transcript = _committed_transcript_messages(self.results)
            if transcript:
                context["_transaction_kernel_prebuilt_messages"] = transcript
        remaining_seconds = self.command.timeout_seconds
        if self.deadline is not None:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("continuous_execution_deadline_exceeded")
            remaining_seconds = max(1, math.ceil(min(remaining, self.command.timeout_seconds or remaining)))
            for key in ("request_timeout_seconds", "timeout_seconds"):
                previous = context.get(key)
                context[key] = (
                    min(float(previous), remaining) if isinstance(previous, int | float) and previous > 0 else remaining
                )
            provider_budget = min(remaining, self.command.timeout_seconds or remaining)
            previous = context.get("llm_call_timeout_seconds")
            context["llm_call_timeout_seconds"] = (
                min(float(previous), provider_budget)
                if isinstance(previous, int | float) and previous > 0
                else provider_budget
            )
        return replace(
            self.command,
            user_message=message,
            context=context,
            metadata=metadata,
            stream=True,
            timeout_seconds=remaining_seconds,
        )

    async def execute_stream(
        self,
        turn_id: str,
        context: list[dict[str, Any]],
        tool_definitions: list[dict[str, Any]],
        *,
        parent_span_id: str | None = None,
    ) -> AsyncGenerator[TurnEvent | dict[str, Any], None]:
        del tool_definitions
        message = next(
            (str(item.get("content") or "") for item in reversed(context) if item.get("role") == "user"),
            self.command.user_message,
        )
        try:
            command = self._turn_command(turn_id, message)
            self.runtime._require_directed_effect_session_attempt(command)
            remaining = self.deadline - time.monotonic() if self.deadline is not None else None
            async with asyncio.timeout(remaining):
                request = await self.runtime._prepare_session_request(command, include_session_snapshot=True)
                kernel = self.runtime._get_kernel(command.workspace)
                final_result = None
                error_result_received = False
                async for event in kernel.run_stream(command.role, request):
                    event_type = str(event.get("type") or "")
                    if event_type in {"content_chunk", "thinking_chunk"}:
                        yield ContentChunkEvent(
                            turn_id=turn_id,
                            chunk=str(event.get("content") or ""),
                            is_thinking=event_type == "thinking_chunk",
                            parent_span_id=parent_span_id,
                        )
                    elif event_type == "error":
                        error = str(event.get("error") or "role_stream_error")
                        candidate = event.get("result")
                        if isinstance(candidate, RoleTurnResult):
                            # Failure projections can carry already committed
                            # writes. Preserve them even when the failure is fatal.
                            final_result = replace(candidate, error=candidate.error or error, is_complete=False)
                            error_result_received = True
                            candidate_metadata = candidate.metadata or {}
                            has_committed_receipt = (
                                bool((candidate.batch_receipt or {}).get("results"))
                                and isinstance(candidate_metadata.get("turn_commit_receipt"), Mapping)
                                and bool(candidate_metadata["turn_commit_receipt"])
                            )
                            recoverable = (
                                str(event.get("error_type")) in {"task_boundary_failed", "stream_execution_failed"}
                                and candidate_metadata.get("transaction_kind") == "continue_multi_turn"
                                and candidate_metadata.get("needs_followup_workflow") is True
                                and has_committed_receipt
                            )
                            if recoverable:
                                continue
                        if self.first_error is None:
                            self.first_error = error
                        yield ErrorEvent(
                            turn_id=turn_id,
                            error_type=str(event.get("error_type") or "role_stream_error"),
                            message=error,
                            parent_span_id=parent_span_id,
                        )
                    elif event_type == "complete" and isinstance(event.get("result"), RoleTurnResult):
                        if not error_result_received:
                            final_result = event["result"]
                    elif event_type in {"tool_call", "tool_result"}:
                        yield dict(event)
                if final_result is None:
                    raise RuntimeError("role_stream_incomplete")
                # The kernel projects every committed invocation from its typed
                # ledger inventory. Started events cover dispatch only and omit
                # denied/superseded members, so they cannot author native history.
                self.results.append(final_result)
                await self.runtime._persist_session_turn_state(
                    command,
                    turn_history=list(final_result.turn_history),
                    turn_events_metadata=list(final_result.turn_events_metadata),
                )
                self.context.update(dict(command.context))
            if self.first_error:
                return
            metadata = final_result.metadata or {}
            kind = str(metadata.get("transaction_kind") or "final_answer")
            followup = kind == "continue_multi_turn" and bool(metadata.get("needs_followup_workflow"))
            # Failed verification is a real failed receipt. It can request a
            # local next turn; policy/authority/provider failures cannot.
            if final_result.error and not followup:
                self.first_error = final_result.error
                yield ErrorEvent(
                    turn_id=turn_id,
                    error_type="role_kernel_failure",
                    message=final_result.error,
                    parent_span_id=parent_span_id,
                )
                return
            if final_result.is_complete and not followup:
                # Completion is scoped to the kernel-owned task boundary. A
                # qualified task need not manufacture another final-answer call.
                kind = "final_answer"
            failure_summary = str(final_result.error or "")
            failed_batch_summary = _failed_batch_feedback_summary(final_result.batch_receipt)
            if failed_batch_summary:
                failure_summary = "\n".join(part for part in (failure_summary, failed_batch_summary) if part)
            verdict = metadata.get("task_boundary_verdict")
            if isinstance(verdict, Mapping):
                for key in (
                    "missing_target_files",
                    "missing_required_evidence_modalities",
                    "failed_required_evidence_modalities",
                    "missing_required_verifiers",
                    "failed_required_verifiers",
                ):
                    values = verdict.get(key)
                    if isinstance(values, list | tuple) and values:
                        details = ", ".join(str(value)[:200] for value in values[:8])
                        failure_summary += f"; {key}={details}"
            yield CompletionEvent(
                turn_id=turn_id,
                status="failed" if final_result.error else "success",
                visible_content=final_result.content,
                turn_kind=kind,
                batch_receipt=dict(final_result.batch_receipt or {}),
                error=final_result.error,
                session_patch={"error_summary": failure_summary} if failure_summary else {},
                turn_outcome=dict(metadata.get("turn_outcome") or {}),
                commit_receipt=dict(metadata.get("turn_commit_receipt") or {}),
                llm_calls=int(final_result.execution_stats.get("llm_calls", 0)),
                tool_calls=int(final_result.execution_stats.get("tool_calls", 0)),
                parent_span_id=parent_span_id,
            )
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            error = str(exc) or (
                "continuous_execution_deadline_exceeded" if isinstance(exc, TimeoutError) else type(exc).__name__
            )
            if self.first_error is None:
                self.first_error = error
            yield ErrorEvent(
                turn_id=turn_id, error_type=type(exc).__name__, message=error, parent_span_id=parent_span_id
            )


async def stream_kernel_session(
    runtime: Any, command: ExecuteRoleSessionCommandV1
) -> AsyncGenerator[dict[str, Any], None]:
    """Project the one existing orchestrator for both public session transports."""
    bridge = KernelSessionTurnBridge(runtime, command)
    policy = command.context.get("continuous_execution")
    # A budgeted canonical task uses its existing irreversible deadline.
    # Explicit caller turn limits remain enforceable; unbudgeted sessions
    # retain the legacy ceiling rather than becoming unbounded.
    max_turns = (
        int(policy["max_auto_turns"])
        if isinstance(policy, Mapping) and "max_auto_turns" in policy
        else None
        if bridge.deadline is not None
        else 10
    )
    orchestrator = RoleSessionOrchestrator(
        command.session_id, bridge, command.workspace, role=command.role, max_auto_turns=max_turns
    )
    stop_reason = None
    async for event in orchestrator.execute_stream(command.user_message, context=dict(command.context)):
        if isinstance(event, dict):
            yield dict(event)
        elif isinstance(event, ContentChunkEvent):
            yield {"type": "thinking_chunk" if event.is_thinking else "content_chunk", "content": event.chunk}
        if isinstance(event, ErrorEvent):
            stop_reason = stop_reason or event.message
            yield {"type": "error", "error": event.message, "error_type": event.error_type}
        elif isinstance(event, SessionWaitingHumanEvent) or (
            isinstance(event, SessionCompletedEvent) and event.reason not in {None, "end_session"}
        ):
            stop_reason = stop_reason or event.reason
    last = bridge.results[-1] if bridge.results else RoleTurnResult(content="", is_complete=False)
    if stop_reason or bridge.first_error:
        last = replace(last, error=stop_reason or bridge.first_error, is_complete=False)
    elif bool(last.metadata.get("needs_followup_workflow")) or not last.is_complete:
        last = replace(last, error="continuous_execution_incomplete", is_complete=False)
    receipts = [dict(result.batch_receipt) for result in bridge.results if result.batch_receipt]
    rows = [dict(row) for receipt in receipts for row in receipt.get("results", []) if isinstance(row, Mapping)]
    batch_projection = {
        "projection_only": True,
        "batch_receipts": receipts,
        "results": rows,
        "success_count": sum(int(receipt.get("success_count", 0)) for receipt in receipts),
        "failure_count": sum(int(receipt.get("failure_count", 0)) for receipt in receipts),
    }
    aggregate_stats = dict(last.execution_stats)
    for key in ("llm_calls", "tool_calls", "duration_ms"):
        aggregate_stats[key] = sum(turn.execution_stats.get(key, 0) for turn in bridge.results)
    last = replace(
        last,
        tool_calls=[item for turn in bridge.results for item in turn.tool_calls],
        tool_results=[item for turn in bridge.results for item in turn.tool_results],
        batch_receipt=batch_projection,
        execution_stats=aggregate_stats,
        turn_history=[item for turn in bridge.results for item in turn.turn_history],
        turn_events_metadata=[item for turn in bridge.results for item in turn.turn_events_metadata],
    )
    last = replace(
        last,
        metadata={
            **dict(last.metadata),
            "continuous_execution": {
                "enabled": True,
                "turns_executed": len(bridge.results),
                "stop_reason": stop_reason,
            },
            "batch_receipt": batch_projection,
            "tool_results": [item for turn in bridge.results for item in turn.tool_results],
            "session_turn_commit_receipts": [
                dict(turn.metadata["turn_commit_receipt"])
                for turn in bridge.results
                if isinstance(turn.metadata.get("turn_commit_receipt"), Mapping)
            ],
        },
    )
    yield {"type": "complete", "result": last, "content": last.content, "metadata": dict(last.metadata)}


async def execute_kernel_session(runtime: Any, command: ExecuteRoleSessionCommandV1) -> RoleExecutionResultV1:
    """Collect the same session projection used by the public streaming facade."""
    from polaris.cells.roles.runtime.public.service import _to_contract_result

    final_result = None
    async for event in stream_kernel_session(runtime, command):
        if event.get("type") == "complete" and isinstance(event.get("result"), RoleTurnResult):
            final_result = event["result"]
    if final_result is None:
        raise RuntimeError("continuous_execution_missing_terminal_projection")
    return _to_contract_result(
        role=command.role,
        workspace=command.workspace,
        task_id=command.task_id,
        session_id=command.session_id,
        run_id=command.run_id,
        result=final_result,
    )
