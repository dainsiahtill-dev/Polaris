"""Adapter-owned convergence verifier factories for Director repair runtime."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from polaris.cells.director.runtime.public import (
    DirectorRepairConvergenceVerifierFn,
    DirectorRepairConvergenceVerifierRequestV1,
    DirectorRepairVerifierSnapshotInputV1,
)
from polaris.kernelone.quality import step_verify as _step_verify_module
from polaris.kernelone.quality.artifact_quality import scan_workspace_artifact_quality
from polaris.kernelone.quality.step_verify import run_step_verify
from polaris.kernelone.storage.layout import resolve_storage_roots

_LOG_FILE_KIND_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_SHELL_COMMAND_PREFIX = ("/bin/sh", "-c")
_STEP_VERIFY_SAFETY_POLICY_NAMES = (
    "assess_step_verify_command_safety",
    "evaluate_step_verify_command_safety",
    "check_step_verify_command_safety",
    "validate_step_verify_command_safety",
    "evaluate_step_verify_safety",
    "check_step_verify_safety",
    "validate_step_verify_safety",
    "step_verify_command_safety_policy",
    "step_verify_safety_policy",
    "STEP_VERIFY_COMMAND_SAFETY_POLICY",
    "STEP_VERIFY_SAFETY_POLICY",
)
_STEP_VERIFY_SAFETY_METHOD_NAMES = ("evaluate", "check", "validate", "is_allowed")
_FALLBACK_UNSAFE_STEP_VERIFY_PATTERNS: tuple[tuple[re.Pattern[str], tuple[str, ...], str], ...] = (
    (
        re.compile(r"\bpython(?:3(?:\.\d+)?)?\s+-c\b"),
        ("python", "-c"),
        "inline Python execution is not allowed for step verification",
    ),
    (
        re.compile(r"\bnode\s+-e\b"),
        ("node", "-e"),
        "inline Node.js execution is not allowed for step verification",
    ),
    (
        re.compile(r"\b(?:bash|sh)\s+-c\b"),
        ("shell", "-c"),
        "nested shell execution is not allowed for step verification",
    ),
    (
        re.compile(r"\brm\b"),
        ("rm",),
        "destructive remove commands are not allowed for step verification",
    ),
    (
        re.compile(r"\b(?:rmdir|mv|dd|chmod|chown|sudo|su|kill|pkill)\b"),
        ("destructive_command",),
        "destructive or privilege-changing commands are not allowed for step verification",
    ),
    (
        re.compile(r"`|\$\("),
        ("command_substitution",),
        "command substitution is not allowed for step verification",
    ),
)


def build_step_verify_convergence_verifier(
    workspace: str | Path,
    *,
    task_id: str,
    verify_command: str | Sequence[str],
    log_root: str | Path | None = None,
) -> DirectorRepairConvergenceVerifierFn:
    """Build a convergence verifier that executes KernelOne step verification."""

    workspace_path = Path(workspace).resolve()
    command_text, input_command = _normalize_verify_command(verify_command)
    command_tuple = (*_SHELL_COMMAND_PREFIX, command_text)

    def _verifier(request: DirectorRepairConvergenceVerifierRequestV1) -> DirectorRepairVerifierSnapshotInputV1:
        metadata: dict[str, Any] = _base_metadata(
            request,
            task_id=task_id,
            workspace_path=workspace_path,
            verifier_factory="step_verify",
            command_kind="shell_step_verify",
            execution_function="polaris.kernelone.quality.step_verify.run_step_verify",
        )
        metadata["verify_command"] = command_text
        metadata["input_command"] = list(input_command)
        environment_prep_receipts, environment_prep_residuals = _execute_environment_prep_plans(
            request,
            workspace_path=workspace_path,
            log_root=log_root,
            task_id=task_id,
        )
        metadata["environment_prep_receipts"] = list(environment_prep_receipts)
        metadata["environment_prep_required"] = bool(request.environment_prep_plans)
        metadata["environment_prep_plan_count"] = len(request.environment_prep_plans)
        metadata["environment_prep_failed"] = bool(environment_prep_residuals)

        exit_code = 1
        output = ""
        residuals: tuple[str, ...]
        if environment_prep_residuals:
            metadata["failure_reason"] = "environment_prep_failed_before_revalidation"
            metadata["revalidation_failure_reason"] = "environment_prep_failed"
            output = "\n".join(environment_prep_residuals)
            residuals = environment_prep_residuals
        elif not command_text:
            metadata["failure_reason"] = "empty_verify_command"
            metadata.update(
                _step_verify_safety_metadata(
                    allowed=False,
                    reason="verify command is empty",
                    blocked_clauses=(),
                    blocked_tokens=(),
                    policy_source="adapter_empty_command_guard",
                )
            )
            residuals = ("Step verify failed: verify command is empty.",)
        else:
            safety = _evaluate_step_verify_command_safety(command_text, workspace_path=workspace_path)
            metadata.update(
                _step_verify_safety_metadata(
                    allowed=bool(safety["allowed"]),
                    reason=str(safety["reason"]),
                    blocked_clauses=_tuple_str(safety["blocked_clauses"]),
                    blocked_tokens=_tuple_str(safety["blocked_tokens"]),
                    policy_source=str(safety["policy_source"]),
                )
            )
            if not bool(safety["allowed"]):
                metadata["failure_reason"] = "step_verify_command_rejected_by_safety_policy"
                reason = str(safety["reason"])
                output = f"Step verify command rejected by safety policy: {reason}\n"
                residuals = (f"Step verify command rejected by safety policy: {reason}",)
            else:
                try:
                    outcome = run_step_verify(command_text, cwd=str(workspace_path))
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    metadata["failure_reason"] = "step_verify_exception"
                    metadata["verifier_error_type"] = type(exc).__name__
                    metadata["verifier_error"] = str(exc)
                    residuals = (f"Step verify failed: {type(exc).__name__}: {exc}",)
                else:
                    if outcome is None:
                        metadata["failure_reason"] = "step_verify_returned_none"
                        output = "Step verify could not run or timed out before producing an exit code."
                        residuals = (f"Step verify failed: command could not run or timed out: {command_text}",)
                    else:
                        exit_code, output = outcome
                        residuals = () if exit_code == 0 else (_step_verify_residual(command_text, exit_code, output),)

        raw_output_ref, log_metadata = _write_raw_output_log(
            workspace_path,
            log_root=log_root,
            task_id=task_id,
            round_number=request.round_number,
            verifier_name="step-verify",
            payload={
                "schema_version": "roles.adapters.repair_convergence_verifier.raw_output.v1",
                "verifier_factory": "step_verify",
                "task_id": task_id,
                "request_task_id": request.task_id,
                "round_number": request.round_number,
                "workspace": str(workspace_path),
                "command": list(command_tuple),
                "input_command": list(input_command),
                "exit_code": exit_code,
                "residual_artifact_quality_errors": list(residuals),
                "environment_prep_receipts": list(environment_prep_receipts),
                "output": output,
                "metadata": metadata,
            },
        )
        metadata.update(log_metadata)
        if raw_output_ref is None:
            residuals = (*residuals, "Step verify failed: raw output log could not be written.")
            exit_code = exit_code if exit_code != 0 else 1
            metadata["revalidation_failure_reason"] = "missing_revalidation_evidence"
            metadata["raw_output_evidence_missing"] = True
        metadata["output_bytes"] = len(output.encode("utf-8", errors="replace"))
        _finalize_revalidation_evidence_metadata(
            metadata,
            command=command_tuple,
            exit_code=exit_code,
            residuals=residuals,
            raw_output_ref=raw_output_ref,
        )

        return DirectorRepairVerifierSnapshotInputV1(
            residual_artifact_quality_errors=residuals,
            command=command_tuple,
            exit_code=exit_code,
            raw_output_ref=raw_output_ref,
            metadata=metadata,
        )

    return _verifier


def build_artifact_quality_convergence_verifier(
    workspace: str | Path,
    *,
    task_id: str,
    relative_paths: Iterable[str | Path] | None = None,
    log_root: str | Path | None = None,
) -> DirectorRepairConvergenceVerifierFn:
    """Build a convergence verifier that runs the in-process artifact quality scan."""

    workspace_path = Path(workspace).resolve()
    normalized_paths, dropped_paths = _normalize_relative_paths(relative_paths)
    command_tuple = _artifact_quality_command_tuple(workspace_path, normalized_paths)

    def _verifier(request: DirectorRepairConvergenceVerifierRequestV1) -> DirectorRepairVerifierSnapshotInputV1:
        metadata: dict[str, Any] = _base_metadata(
            request,
            task_id=task_id,
            workspace_path=workspace_path,
            verifier_factory="artifact_quality",
            command_kind="in_process_artifact_quality_scan",
            execution_function="polaris.kernelone.quality.artifact_quality.scan_workspace_artifact_quality",
        )
        metadata["relative_paths"] = list(normalized_paths) if normalized_paths is not None else None
        metadata["dropped_unsafe_relative_paths"] = list(dropped_paths)
        environment_prep_receipts, environment_prep_residuals = _execute_environment_prep_plans(
            request,
            workspace_path=workspace_path,
            log_root=log_root,
            task_id=task_id,
        )
        metadata["environment_prep_receipts"] = list(environment_prep_receipts)
        metadata["environment_prep_required"] = bool(request.environment_prep_plans)
        metadata["environment_prep_plan_count"] = len(request.environment_prep_plans)
        metadata["environment_prep_failed"] = bool(environment_prep_residuals)

        residuals: tuple[str, ...]
        if environment_prep_residuals:
            metadata["failure_reason"] = "environment_prep_failed_before_revalidation"
            metadata["revalidation_failure_reason"] = "environment_prep_failed"
            residuals = environment_prep_residuals
            exit_code = 1
        else:
            try:
                scan_errors = scan_workspace_artifact_quality(
                    str(workspace_path),
                    relative_paths=normalized_paths,
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                metadata["failure_reason"] = "artifact_quality_scan_exception"
                metadata["verifier_error_type"] = type(exc).__name__
                metadata["verifier_error"] = str(exc)
                scan_errors = [f"Artifact quality scan failed: {type(exc).__name__}: {exc}"]
            residuals = tuple(str(item) for item in scan_errors)
            if dropped_paths:
                residuals = (
                    *residuals,
                    *(
                        f"Artifact quality scan failed: unsafe relative path ignored: {path}"
                        for path in dropped_paths
                    ),
                )
            exit_code = 0 if not residuals else 1

        raw_output_ref, log_metadata = _write_raw_output_log(
            workspace_path,
            log_root=log_root,
            task_id=task_id,
            round_number=request.round_number,
            verifier_name="artifact-quality",
            payload={
                "schema_version": "roles.adapters.repair_convergence_verifier.raw_output.v1",
                "verifier_factory": "artifact_quality",
                "task_id": task_id,
                "request_task_id": request.task_id,
                "round_number": request.round_number,
                "workspace": str(workspace_path),
                "command": list(command_tuple),
                "exit_code": exit_code,
                "relative_paths": list(normalized_paths) if normalized_paths is not None else None,
                "dropped_unsafe_relative_paths": list(dropped_paths),
                "residual_artifact_quality_errors": list(residuals),
                "environment_prep_receipts": list(environment_prep_receipts),
                "metadata": metadata,
            },
        )
        metadata.update(log_metadata)
        if raw_output_ref is None:
            residuals = (*residuals, "Artifact quality scan failed: raw output log could not be written.")
            exit_code = exit_code if exit_code != 0 else 1
            metadata["revalidation_failure_reason"] = "missing_revalidation_evidence"
            metadata["raw_output_evidence_missing"] = True
        metadata["error_count"] = len(residuals)
        _finalize_revalidation_evidence_metadata(
            metadata,
            command=command_tuple,
            exit_code=exit_code,
            residuals=residuals,
            raw_output_ref=raw_output_ref,
        )

        return DirectorRepairVerifierSnapshotInputV1(
            residual_artifact_quality_errors=residuals,
            command=command_tuple,
            exit_code=exit_code,
            raw_output_ref=raw_output_ref,
            metadata=metadata,
        )

    return _verifier


def _execute_environment_prep_plans(
    request: DirectorRepairConvergenceVerifierRequestV1,
    *,
    workspace_path: Path,
    log_root: str | Path | None,
    task_id: str,
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    receipts: list[dict[str, Any]] = []
    residuals: list[str] = []
    for plan in request.environment_prep_plans:
        receipt = _execute_environment_prep_plan(
            plan.to_dict(),
            workspace_path=workspace_path,
            log_root=log_root,
            task_id=task_id,
            round_number=request.round_number,
        )
        receipts.append(receipt)
        if receipt.get("status") not in {"succeeded", "skipped_fresh"}:
            command = " ".join(str(part) for part in receipt.get("command") or ())
            error_code = str(receipt.get("error_code") or "environment_prep_failed")
            residuals.append(f"Environment prep failed before revalidation: {error_code}: {command}")
    return tuple(receipts), tuple(residuals)


def _execute_environment_prep_plan(
    plan: Mapping[str, Any],
    *,
    workspace_path: Path,
    log_root: str | Path | None,
    task_id: str,
    round_number: int,
) -> dict[str, Any]:
    command = tuple(str(part) for part in plan.get("command") or () if str(part or "").strip())
    manifest = str(plan.get("manifest") or "").strip().replace("\\", "/")
    lockfile = str(plan.get("lockfile") or "").strip().replace("\\", "/")
    plan_id = str(plan.get("plan_id") or "").strip()
    ecosystem = str(plan.get("ecosystem") or "").strip()
    package_manager = str(plan.get("package_manager") or "").strip()
    freshness_key = str(plan.get("freshness_key") or "").strip()
    started = time.monotonic()
    manifest_before = _hash_workspace_file(workspace_path, manifest)
    lockfile_before = _hash_workspace_file(workspace_path, lockfile)
    validation_error = _environment_prep_plan_validation_error(plan)
    output = ""
    stdout = ""
    stderr = ""
    exit_code: int | None = None
    status = "failed"
    error_code = validation_error or ""
    cwd = workspace_path

    if validation_error is None:
        try:
            cwd = _environment_prep_cwd(workspace_path, str(plan.get("cwd") or "."))
            completed = subprocess.run(
                list(command),
                cwd=str(cwd),
                text=True,
                capture_output=True,
                timeout=max(1, int(plan.get("timeout_seconds") or 120)),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = str(exc.stdout or "")
            stderr = str(exc.stderr or "")
            output = f"Environment prep timed out: {' '.join(command)}"
            exit_code = 124
            error_code = "environment_prep_timeout"
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            stderr = f"{type(exc).__name__}: {exc}"
            output = f"Environment prep could not execute: {stderr}"
            error_code = "environment_prep_execution_error"
        else:
            stdout = str(completed.stdout or "")
            stderr = str(completed.stderr or "")
            output = stdout + ("\n" if stdout and stderr else "") + stderr
            exit_code = int(completed.returncode)
            status = "succeeded" if exit_code == 0 else "failed"
            error_code = "" if exit_code == 0 else "environment_prep_command_failed"

    duration_ms = int((time.monotonic() - started) * 1000)
    manifest_after = _hash_workspace_file(workspace_path, manifest)
    lockfile_after = _hash_workspace_file(workspace_path, lockfile)
    raw_output_ref, log_metadata = _write_raw_output_log(
        workspace_path,
        log_root=log_root,
        task_id=task_id,
        round_number=round_number,
        verifier_name="environment-prep",
        payload={
            "schema_version": "roles.adapters.environment_prep.raw_output.v1",
            "plan": dict(plan),
            "workspace": str(workspace_path),
            "cwd": str(cwd),
            "command": list(command),
            "exit_code": exit_code,
            "status": status,
            "stdout": stdout,
            "stderr": stderr,
            "output": output,
            "duration_ms": duration_ms,
            "error_code": error_code,
        },
    )
    return {
        "schema_version": "director.environment_prep_receipt.v1",
        "plan_id": plan_id,
        "ecosystem": ecosystem,
        "package_manager": package_manager,
        "command": list(command),
        "exit_code": exit_code,
        "status": status,
        "duration_ms": duration_ms,
        "manifest": manifest,
        "lockfile": lockfile,
        "manifest_hash_before": manifest_before,
        "manifest_hash_after": manifest_after,
        "lockfile_hash_before": lockfile_before,
        "lockfile_hash_after": lockfile_after,
        "stdout_ref": raw_output_ref or "",
        "stderr_ref": raw_output_ref or "",
        "freshness_key": freshness_key,
        "error_code": error_code,
        "authoritative_repair": False,
        "metadata": {
            **log_metadata,
            "adapter_module": "polaris.cells.roles.adapters.internal.director.repair_convergence_verifier",
            "effect_boundary": "adapter_environment_prep_runner_binding",
            "command_source": "director.runtime.environment_prep_catalog",
            "llm_generated_command_allowed": False,
            "agi_execution_authority": False,
        },
    }


def _environment_prep_plan_validation_error(plan: Mapping[str, Any]) -> str | None:
    policy = dict(plan.get("policy") or {})
    command = tuple(str(part) for part in plan.get("command") or () if str(part or "").strip())
    if str(plan.get("schema_version") or "") != "director.environment_prep_plan.v1":
        return "invalid_environment_prep_plan_schema"
    if not command:
        return "environment_prep_command_missing"
    if str(policy.get("command_source") or "") != "director.runtime.environment_prep_catalog":
        return "environment_prep_command_not_from_runtime_catalog"
    if bool(policy.get("llm_generated_command_allowed")):
        return "environment_prep_llm_generated_command_not_allowed"
    if bool(policy.get("agi_execution_authority")):
        return "environment_prep_agi_execution_not_allowed"
    if bool(policy.get("authoritative_repair")):
        return "environment_prep_cannot_be_authoritative_repair"
    if bool(policy.get("global_writes_allowed")):
        return "environment_prep_global_writes_not_allowed"
    return None


def _environment_prep_cwd(workspace_path: Path, cwd: str) -> Path:
    normalized = str(cwd or ".").strip()
    candidate = Path(normalized)
    if candidate.is_absolute():
        raise ValueError("environment prep cwd must be workspace-relative")
    resolved = (workspace_path / candidate).resolve()
    resolved.relative_to(workspace_path)
    return resolved


def _hash_workspace_file(workspace_path: Path, rel_path: str) -> str:
    normalized = str(rel_path or "").strip().replace("\\", "/")
    if not normalized:
        return ""
    target = (workspace_path / normalized).resolve()
    try:
        target.relative_to(workspace_path)
        return _sha256_text(target.read_text(encoding="utf-8")) if target.is_file() else ""
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return ""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _base_metadata(
    request: DirectorRepairConvergenceVerifierRequestV1,
    *,
    task_id: str,
    workspace_path: Path,
    verifier_factory: str,
    command_kind: str,
    execution_function: str,
) -> dict[str, Any]:
    request_workspace = str(request.workspace or "")
    return {
        "adapter_module": "polaris.cells.roles.adapters.internal.director.repair_convergence_verifier",
        "evidence_source": "adapter_convergence_verifier_factory",
        "verifier_factory": verifier_factory,
        "command_kind": command_kind,
        "execution_function": execution_function,
        "factory_task_id": str(task_id),
        "request_task_id": request.task_id,
        "task_id_match": request.task_id == str(task_id),
        "round_number": request.round_number,
        "max_rounds": request.max_rounds,
        "source_tools": list(request.source_tools),
        "receipt_count": len(request.receipts),
        "environment_prep_plan_count": len(request.environment_prep_plans),
        "workspace": str(workspace_path),
        "request_workspace": request_workspace,
        "workspace_match": request_workspace == str(workspace_path),
    }


def _normalize_verify_command(verify_command: str | Sequence[str]) -> tuple[str, tuple[str, ...]]:
    if isinstance(verify_command, str):
        command_text = verify_command.strip()
        return command_text, tuple(shlex.split(command_text)) if command_text else ()
    parts = tuple(str(part).strip() for part in verify_command if str(part).strip())
    return shlex.join(parts), parts


def _evaluate_step_verify_command_safety(command_text: str, *, workspace_path: Path) -> dict[str, Any]:
    resolved_policy = _resolve_step_verify_safety_policy()
    if resolved_policy is None:
        return _fallback_step_verify_command_safety(command_text)
    policy_name, policy = resolved_policy
    try:
        decision = _invoke_step_verify_safety_policy(policy, command_text, workspace_path=workspace_path)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "allowed": False,
            "reason": f"step_verify safety policy failed closed: {type(exc).__name__}: {exc}",
            "blocked_clauses": (command_text,),
            "blocked_tokens": ("policy_error",),
            "policy_source": "kernelone_step_verify_policy_error",
        }
    return _coerce_step_verify_safety_decision(
        decision,
        policy_source=f"kernelone_step_verify.{policy_name}",
    )


def _resolve_step_verify_safety_policy() -> tuple[str, Any] | None:
    for name in _STEP_VERIFY_SAFETY_POLICY_NAMES:
        candidate = getattr(_step_verify_module, name, None)
        if candidate is not None:
            return name, candidate
    return None


def _invoke_step_verify_safety_policy(policy: Any, command_text: str, *, workspace_path: Path) -> Any:
    callable_policy = policy if callable(policy) else None
    if callable_policy is None:
        for method_name in _STEP_VERIFY_SAFETY_METHOD_NAMES:
            method = getattr(policy, method_name, None)
            if callable(method):
                callable_policy = method
                break
    if callable_policy is None:
        raise TypeError(f"step_verify safety policy is not callable: {type(policy).__name__}")

    attempts: tuple[tuple[tuple[str, ...], dict[str, str]], ...] = (
        ((), {"command": command_text, "cwd": str(workspace_path)}),
        ((), {"verify": command_text, "cwd": str(workspace_path)}),
        ((), {"command": command_text, "workspace": str(workspace_path)}),
        ((), {"verify": command_text, "workspace": str(workspace_path)}),
        ((command_text,), {"cwd": str(workspace_path)}),
        ((command_text,), {"workspace": str(workspace_path)}),
        ((command_text, str(workspace_path)), {}),
        ((command_text,), {}),
    )
    last_type_error: TypeError | None = None
    for args, kwargs in attempts:
        try:
            return callable_policy(*args, **kwargs)
        except TypeError as exc:
            last_type_error = exc
            continue
    if last_type_error is not None:
        raise last_type_error
    raise TypeError("step_verify safety policy could not be invoked")


def _coerce_step_verify_safety_decision(decision: Any, *, policy_source: str) -> dict[str, Any]:
    if isinstance(decision, bool):
        return {
            "allowed": decision,
            "reason": "allowed by step_verify safety policy" if decision else "blocked by step_verify safety policy",
            "blocked_clauses": (),
            "blocked_tokens": (),
            "policy_source": policy_source,
        }
    if isinstance(decision, tuple) and decision:
        allowed = bool(decision[0])
        reason = str(decision[1]) if len(decision) > 1 and decision[1] else _default_safety_reason(allowed)
        blocked_clauses = _tuple_str(decision[2]) if len(decision) > 2 else ()
        blocked_tokens = _tuple_str(decision[3]) if len(decision) > 3 else ()
        decision_policy_source = str(decision[4]) if len(decision) > 4 and decision[4] else policy_source
        return {
            "allowed": allowed,
            "reason": reason,
            "blocked_clauses": blocked_clauses,
            "blocked_tokens": blocked_tokens,
            "policy_source": decision_policy_source,
        }

    allowed = _decision_attr(decision, "allowed", "is_safe", "safe", "ok")
    decision_policy_source = _decision_attr(decision, "policy_source", "source") or policy_source
    if not isinstance(allowed, bool):
        return {
            "allowed": False,
            "reason": "step_verify safety policy returned an unrecognized decision shape",
            "blocked_clauses": (),
            "blocked_tokens": ("unrecognized_policy_decision",),
            "policy_source": str(decision_policy_source),
        }
    reason = _decision_attr(decision, "reason", "message", "error") or _default_safety_reason(allowed)
    blocked_clauses = _tuple_str(_decision_attr(decision, "blocked_clauses", "unsafe_clauses"))
    blocked_tokens = _tuple_str(_decision_attr(decision, "blocked_tokens", "unsafe_tokens"))
    return {
        "allowed": allowed,
        "reason": str(reason),
        "blocked_clauses": blocked_clauses,
        "blocked_tokens": blocked_tokens,
        "policy_source": str(decision_policy_source),
    }


def _decision_attr(decision: Any, *names: str) -> Any:
    if isinstance(decision, Mapping):
        for name in names:
            if name in decision:
                return decision[name]
        return None
    for name in names:
        if hasattr(decision, name):
            return getattr(decision, name)
    return None


def _fallback_step_verify_command_safety(command_text: str) -> dict[str, Any]:
    command = str(command_text or "").strip()
    if not command:
        return {
            "allowed": False,
            "reason": "verify command is empty",
            "blocked_clauses": (),
            "blocked_tokens": (),
            "policy_source": "adapter_fallback_step_verify_safety_policy",
        }

    blocked_clauses: list[str] = []
    blocked_tokens: list[str] = []
    reasons: list[str] = []
    for pattern, tokens, reason in _FALLBACK_UNSAFE_STEP_VERIFY_PATTERNS:
        for clause in _split_step_verify_safety_clauses(command):
            if not pattern.search(clause):
                continue
            blocked_clauses.append(clause)
            blocked_tokens.extend(tokens)
            reasons.append(reason)
    if blocked_clauses:
        return {
            "allowed": False,
            "reason": "; ".join(dict.fromkeys(reasons)),
            "blocked_clauses": tuple(dict.fromkeys(blocked_clauses)),
            "blocked_tokens": tuple(dict.fromkeys(blocked_tokens)),
            "policy_source": "adapter_fallback_step_verify_safety_policy",
        }
    return {
        "allowed": True,
        "reason": "allowed by adapter fallback step_verify safety policy",
        "blocked_clauses": (),
        "blocked_tokens": (),
        "policy_source": "adapter_fallback_step_verify_safety_policy",
    }


def _split_step_verify_safety_clauses(command_text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in re.split(r"\s*(?:&&|\|\|)\s*", command_text) if part.strip())


def _step_verify_safety_metadata(
    *,
    allowed: bool,
    reason: str,
    blocked_clauses: tuple[str, ...],
    blocked_tokens: tuple[str, ...],
    policy_source: str,
) -> dict[str, Any]:
    return {
        "command_safety_allowed": bool(allowed),
        "command_safety_reason": str(reason),
        "blocked_clauses": list(blocked_clauses),
        "blocked_tokens": list(blocked_tokens),
        "command_safety_policy_source": str(policy_source),
        "step_verify_safety_policy_source": str(policy_source),
    }


def _finalize_revalidation_evidence_metadata(
    metadata: dict[str, Any],
    *,
    command: Sequence[str],
    exit_code: int | None,
    residuals: Sequence[str],
    raw_output_ref: str | None,
) -> None:
    if raw_output_ref is None:
        metadata.setdefault("revalidation_failure_reason", "missing_revalidation_evidence")
        metadata["raw_output_evidence_missing"] = True
        metadata["evidence_status"] = "missing_evidence"
        return
    if not command or exit_code is None:
        metadata.setdefault("revalidation_failure_reason", "missing_revalidation_evidence")
        metadata["evidence_status"] = "missing_evidence"
        return
    metadata["evidence_status"] = "failed_evidence" if exit_code != 0 or residuals else "resolved_evidence"


def _default_safety_reason(allowed: bool) -> str:
    return "allowed by step_verify safety policy" if allowed else "blocked by step_verify safety policy"


def _tuple_str(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    try:
        values = tuple(value)
    except TypeError:
        return (str(value),)
    return tuple(str(item) for item in values if str(item))


def _step_verify_residual(command_text: str, exit_code: int, output: str) -> str:
    first_line = _first_nonempty_line(output)
    detail = f": {first_line}" if first_line else ""
    return f"Step verify failed: command exited with code {exit_code}: {command_text}{detail}"


def _first_nonempty_line(output: str) -> str:
    for line in str(output or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:400]
    return ""


def _normalize_relative_paths(
    relative_paths: Iterable[str | Path] | None,
) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
    if relative_paths is None:
        return None, ()
    safe_paths: list[str] = []
    dropped_paths: list[str] = []
    seen: set[str] = set()
    for raw_path in relative_paths:
        normalized = str(raw_path or "").strip().replace("\\", "/")
        if not normalized:
            continue
        candidate = PurePosixPath(normalized)
        if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
            dropped_paths.append(normalized)
            continue
        collapsed = candidate.as_posix()
        if collapsed in {"", "."}:
            continue
        if collapsed in seen:
            continue
        seen.add(collapsed)
        safe_paths.append(collapsed)
    return tuple(safe_paths), tuple(dropped_paths)


def _artifact_quality_command_tuple(workspace_path: Path, relative_paths: tuple[str, ...] | None) -> tuple[str, ...]:
    command = [
        "polaris.kernelone.quality.artifact_quality.scan_workspace_artifact_quality",
        str(workspace_path),
    ]
    if relative_paths is None:
        command.append("--all-source-files")
    else:
        command.append("--paths")
        command.extend(relative_paths)
    return tuple(command)


def _write_raw_output_log(
    workspace_path: Path,
    *,
    log_root: str | Path | None,
    task_id: str,
    round_number: int,
    verifier_name: str,
    payload: Mapping[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    metadata: dict[str, Any] = {
        "raw_output_ref_kind": "absolute_path",
        "raw_output_log_attempted": True,
    }
    attempts: list[str] = []
    for candidate_root, candidate_metadata in _log_root_candidates(workspace_path, log_root):
        attempts.append(str(candidate_root))
        filename = _log_filename(task_id=task_id, round_number=round_number, verifier_name=verifier_name)
        try:
            resolved_root = candidate_root.resolve()
            resolved_root.mkdir(parents=True, exist_ok=True)
            output_path = (resolved_root / filename).resolve()
            output_path.relative_to(resolved_root)
            output_path.write_text(
                json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            metadata["raw_output_write_failed"] = True
            metadata["raw_output_write_error_type"] = type(exc).__name__
            metadata["raw_output_write_error"] = str(exc)
            continue
        metadata.update(candidate_metadata)
        metadata["raw_output_log_path"] = str(output_path)
        metadata["raw_output_log_attempts"] = attempts
        metadata["raw_output_write_failed"] = False
        metadata["raw_output_ref_verified"] = True
        return str(output_path), metadata
    metadata["raw_output_log_attempts"] = attempts
    metadata["raw_output_ref_verified"] = False
    return None, metadata


def _log_root_candidates(workspace_path: Path, log_root: str | Path | None) -> list[tuple[Path, dict[str, Any]]]:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    seen: set[str] = set()

    if log_root is not None:
        _append_log_candidate(
            candidates,
            seen,
            Path(log_root),
            {"log_storage": "caller_log_root"},
        )

    runtime_root = _runtime_log_root(workspace_path)
    if runtime_root is not None:
        _append_log_candidate(
            candidates,
            seen,
            runtime_root,
            {"log_storage": "kernelone_runtime_root"},
        )

    _append_log_candidate(
        candidates,
        seen,
        workspace_path / ".polaris" / "repair-verifier",
        {
            "log_storage": "local_workspace_log",
            "local_workspace_log": True,
        },
    )
    return candidates


def _append_log_candidate(
    candidates: list[tuple[Path, dict[str, Any]]],
    seen: set[str],
    path: Path,
    metadata: dict[str, Any],
) -> None:
    try:
        resolved = path.expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        resolved = path
    key = str(resolved)
    if key in seen:
        return
    seen.add(key)
    candidates.append((resolved, metadata))


def _runtime_log_root(workspace_path: Path) -> Path | None:
    try:
        roots = resolve_storage_roots(str(workspace_path))
        runtime_root = Path(str(roots.runtime_root)).expanduser().resolve()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None
    return runtime_root / "logs" / "repair-verifier"


def _log_filename(*, task_id: str, round_number: int, verifier_name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    task_slug = _safe_slug(task_id) or "task"
    verifier_slug = _safe_slug(verifier_name) or "verifier"
    return f"{task_slug}-round-{max(0, int(round_number))}-{verifier_slug}-{timestamp}-{uuid4().hex[:8]}.json"


def _safe_slug(value: str) -> str:
    slug = _LOG_FILE_KIND_RE.sub("-", str(value or "").strip())[:80].strip(".-")
    return slug


__all__ = [
    "build_artifact_quality_convergence_verifier",
    "build_step_verify_convergence_verifier",
]
