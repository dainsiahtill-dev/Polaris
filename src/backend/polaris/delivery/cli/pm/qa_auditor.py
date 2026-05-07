"""Contract-driven Auditor (QA) runtime for PM engine.

This module intentionally avoids LLM verdicts. QA decisions are derived from
deterministic gates + evidence checks.
"""

from __future__ import annotations

import os
import re
from typing import Any

from polaris.delivery.cli.pm.utils import normalize_path_list, normalize_str_list
from polaris.kernelone.process.command_executor import CommandExecutionService
from polaris.kernelone.runtime.shared_types import normalize_path

_ALLOWED_QA_MODES = {"off", "shadow", "blocking"}
_DEFAULT_QA_MODE = "blocking"
_DEFAULT_PLUGIN = "rules_v1"
_DEFAULT_MAX_DIRECTOR_RETRIES = 5
_ALLOWED_VERDICTS = {"PASS", "FAIL", "INCONCLUSIVE", "CONTINUE"}
_DEFAULT_COORDINATION_MAX_ROUNDS = 2
_DEFAULT_COORDINATION_TRIGGERS = (
    "complex_task",
    "qa_fail",
    "qa_inconclusive",
)


def _normalize_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def normalize_qa_mode(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in _ALLOWED_QA_MODES:
        return token
    return _DEFAULT_QA_MODE


def _normalize_retry_policy(value: Any) -> dict[str, int]:
    policy = value if isinstance(value, dict) else {}
    raw = policy.get("max_director_retries", _DEFAULT_MAX_DIRECTOR_RETRIES)
    try:
        parsed = int(raw)
    except (RuntimeError, ValueError):
        parsed = _DEFAULT_MAX_DIRECTOR_RETRIES
    if parsed < 1:
        parsed = _DEFAULT_MAX_DIRECTOR_RETRIES
    return {"max_director_retries": parsed}


def _normalize_coordination_policy(value: Any) -> dict[str, Any]:
    policy = value if isinstance(value, dict) else {}
    enabled = _normalize_bool(policy.get("enabled"), default=True)
    raw_rounds = policy.get("max_rounds", _DEFAULT_COORDINATION_MAX_ROUNDS)
    try:
        max_rounds = int(raw_rounds)
    except (RuntimeError, ValueError):
        max_rounds = _DEFAULT_COORDINATION_MAX_ROUNDS
    if max_rounds < 1:
        max_rounds = _DEFAULT_COORDINATION_MAX_ROUNDS

    raw_triggers = policy.get("triggers")
    if isinstance(raw_triggers, list):
        triggers = [str(item or "").strip().lower() for item in raw_triggers if str(item or "").strip()]
    else:
        triggers = []
    if not triggers:
        triggers = list(_DEFAULT_COORDINATION_TRIGGERS)

    # Keep trigger order stable while deduping.
    stable_triggers: list[str] = []
    for trigger in triggers:
        if trigger not in stable_triggers:
            stable_triggers.append(trigger)

    return {
        "enabled": enabled,
        "max_rounds": max_rounds,
        "triggers": stable_triggers,
    }


def _normalize_gate_spec(value: Any) -> Any | None:
    if isinstance(value, str):
        token = str(value).strip()
        return token if token else None
    if isinstance(value, dict):
        kind = str(value.get("kind") or "").strip().lower()
        if not kind:
            return None
        spec = dict(value)
        spec["kind"] = kind
        if "path" in spec:
            spec["path"] = normalize_path(str(spec.get("path") or ""))
        return spec
    return None


def _normalize_gate_list(value: Any) -> list[Any]:
    items = value if isinstance(value, list) else []
    normalized: list[Any] = []
    for item in items:
        parsed = _normalize_gate_spec(item)
        if parsed is None:
            continue
        if parsed not in normalized:
            normalized.append(parsed)
    return normalized


def _normalize_evidence_required(value: Any) -> list[str]:
    return normalize_path_list(value)


def _looks_like_ui_or_3d_task(task_type: str) -> bool:
    token = str(task_type or "").strip().lower()
    if not token:
        return False
    if token.startswith("ui_") or token.startswith("3d_"):
        return True
    return token in {
        "ui",
        "frontend",
        "canvas",
        "ui_canvas",
        "three",
        "webgl",
        "scene",
    }


def _default_task_type(task: dict[str, Any]) -> str:
    raw = str(task.get("task_type") or task.get("kind") or "").strip().lower()
    if raw:
        return raw
    title = str(task.get("title") or "").strip().lower()
    goal = str(task.get("goal") or "").strip().lower()
    combined = f"{title} {goal}"
    tokens = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", combined))
    ui_signal_tokens = {
        "ui",
        "canvas",
        "frontend",
        "webgl",
        "3d",
        "threejs",
        "three_js",
        "three.js",
        "界面",
        "前端",
        "画布",
    }
    if tokens.intersection(ui_signal_tokens):
        return "ui_canvas"
    # Avoid false positives from phrases like "three arguments". Treat plain
    # "three" as UI only when other 3D/web graphics terms are present.
    if "three" in tokens and tokens.intersection({"canvas", "webgl", "scene", "3d"}):
        return "ui_canvas"
    return "generic"


def _tail_non_empty_lines(text: str, *, limit: int = 8) -> list[str]:
    lines = [str(item).rstrip() for item in str(text or "").splitlines() if str(item).strip()]
    if len(lines) <= limit:
        return lines
    return lines[-limit:]


def _resolve_verify_runs_bucket(context: dict[str, Any]) -> list[dict[str, Any]]:
    payload = context.get("_qa_verify_runs")
    if isinstance(payload, list):
        return payload
    payload = []
    context["_qa_verify_runs"] = payload
    return payload


def _execute_verify_command(
    *,
    command: str,
    working_dir: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    executor = CommandExecutionService(working_dir)
    try:
        request = executor.parse_command(
            command,
            cwd=working_dir,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as exc:
        return {
            "command": command,
            "working_dir": working_dir,
            "timeout_seconds": timeout_seconds,
            "exit_code": 2,
            "stdout_tail": [],
            "stderr_tail": [f"invalid verify command: {exc}"],
            "deferred": False,
            "command_args": [],
        }

    record: dict[str, Any] = {
        "command": command,
        "command_args": [request.executable, *request.args],
        "working_dir": working_dir,
        "timeout_seconds": timeout_seconds,
        "exit_code": -1,
        "stdout_tail": [],
        "stderr_tail": [],
        "deferred": False,
    }
    result = executor.run(request)
    timed_out = bool(result.get("timed_out"))
    record["exit_code"] = 124 if timed_out else int(result.get("returncode", 1))
    record["stdout_tail"] = _tail_non_empty_lines(str(result.get("stdout") or ""), limit=6)
    stderr = str(result.get("stderr") or "")
    if result.get("error"):
        stderr = f"{stderr}\n{result['error']}" if stderr else str(result["error"])
    record["stderr_tail"] = _tail_non_empty_lines(stderr, limit=6)
    return record


def normalize_qa_contract(raw_contract: Any, *, task: dict[str, Any] | None = None) -> dict[str, Any]:
    source_task = task if isinstance(task, dict) else {}
    assigned_to = str(source_task.get("assigned_to") or "").strip()
    task_type = _default_task_type(source_task)

    if not isinstance(raw_contract, dict):
        # Compatibility default: keep legacy flows running while moving to
        # explicit PM-authored qa_contract.
        if assigned_to and assigned_to != "Director":
            return {}
        return {
            "schema_version": 1,
            "source": "auto_compat",
            "plugin": _DEFAULT_PLUGIN,
            "plugin_hint": _DEFAULT_PLUGIN,
            "task_type": task_type,
            "hard_gates": ["director_status_success"],
            "regression_gates": [],
            "evidence_required": [],
            "retry_policy": {
                "max_director_retries": _DEFAULT_MAX_DIRECTOR_RETRIES,
            },
            "coordination": _normalize_coordination_policy(None),
        }

    try:
        schema_version = int(raw_contract.get("schema_version", 1))
    except (RuntimeError, ValueError):
        schema_version = 1
    if schema_version <= 0:
        schema_version = 1

    plugin_hint = (
        str(raw_contract.get("plugin_hint") or raw_contract.get("plugin") or _DEFAULT_PLUGIN).strip().lower()
        or _DEFAULT_PLUGIN
    )
    plugin = str(raw_contract.get("plugin") or plugin_hint).strip().lower() or _DEFAULT_PLUGIN
    contract_task_type = str(raw_contract.get("task_type") or "").strip().lower() or task_type
    hard_gates = _normalize_gate_list(raw_contract.get("hard_gates"))
    if not hard_gates:
        hard_gates = ["director_status_success"]
    regression_gates = _normalize_gate_list(raw_contract.get("regression_gates"))
    evidence_required = _normalize_evidence_required(raw_contract.get("evidence_required"))
    retry_policy = _normalize_retry_policy(raw_contract.get("retry_policy"))
    coordination = _normalize_coordination_policy(raw_contract.get("coordination"))

    source = str(raw_contract.get("source") or "pm_contract").strip() or "pm_contract"

    return {
        "schema_version": schema_version,
        "source": source,
        "plugin": plugin,
        "plugin_hint": plugin_hint,
        "task_type": contract_task_type,
        "hard_gates": hard_gates,
        "regression_gates": regression_gates,
        "evidence_required": evidence_required,
        "retry_policy": retry_policy,
        "coordination": coordination,
    }


def _resolve_runtime_root_from_run_dir(run_dir: str) -> str:
    run_dir_full = os.path.abspath(str(run_dir or "").strip())
    # expected shape: .../<runtime_root>/runs/<run_id>
    if os.path.basename(os.path.dirname(run_dir_full)).lower() == "runs":
        return os.path.dirname(os.path.dirname(run_dir_full))
    return os.path.dirname(run_dir_full)


def _resolve_evidence_path(
    item: str,
    *,
    workspace_full: str,
    run_dir: str,
    evidence_index: dict[str, str],
) -> str:
    token = str(item or "").strip()
    if not token:
        return ""
    if token in evidence_index:
        return str(evidence_index[token] or "").strip()

    if os.path.isabs(token):
        return os.path.abspath(token)

    normalized = normalize_path(token)
    if not normalized:
        return ""

    runtime_root = _resolve_runtime_root_from_run_dir(run_dir)
    if normalized == "runtime":
        return runtime_root
    if normalized.startswith("runtime/"):
        return os.path.join(runtime_root, normalized[len("runtime/") :].replace("/", os.sep))
    if normalized == "workspace":
        return workspace_full
    if normalized.startswith("workspace/"):
        return os.path.join(
            workspace_full,
            normalized[len("workspace/") :].replace("/", os.sep),
        )
    return os.path.join(workspace_full, normalized.replace("/", os.sep))


def _read_source_value(context: dict[str, Any], source: str) -> dict[str, Any]:
    token = str(source or "").strip().lower()
    if token in {"director_result", "result", "payload"}:
        payload = context.get("director_result")
        return payload if isinstance(payload, dict) else {}
    if token in {"task", "pm_task"}:
        payload = context.get("task")
        return payload if isinstance(payload, dict) else {}
    return {}


def _evaluate_gate(
    gate: Any,
    *,
    context: dict[str, Any],
    workspace_full: str,
    run_dir: str,
) -> tuple[bool, str]:
    director_status = str(context.get("director_status") or "").strip().lower()
    changed_files = normalize_path_list(context.get("changed_files") or [])
    evidence_index = context.get("evidence_index")
    evidence_index = evidence_index if isinstance(evidence_index, dict) else {}

    if isinstance(gate, str):
        token = gate.strip().lower()
        if token == "director_status_success":
            return director_status == "success", token
        if token == "changed_files_present":
            return len(changed_files) > 0, token
        if token == "acceptance_defined":
            task = context.get("task")
            accepted = normalize_str_list(task.get("acceptance_criteria") if isinstance(task, dict) else [])
            return len(accepted) > 0, token
        if token == "verify_command_success":
            return False, "verify_command_success_missing_spec"
        return False, f"unknown_gate:{token}"

    if isinstance(gate, dict):
        kind = str(gate.get("kind") or "").strip().lower()
        if kind == "director_status_in":
            allow = [str(x).strip().lower() for x in (gate.get("allow") or []) if str(x).strip()]
            if not allow:
                allow = ["success"]
            return director_status in allow, "director_status_in"
        if kind == "changed_files_min":
            try:
                minimum = int(gate.get("min", 1))
            except (RuntimeError, ValueError):
                minimum = 1
            minimum = max(minimum, 0)
            return len(changed_files) >= minimum, "changed_files_min"
        if kind == "file_exists":
            path = _resolve_evidence_path(
                str(gate.get("path") or ""),
                workspace_full=workspace_full,
                run_dir=run_dir,
                evidence_index=evidence_index,
            )
            return bool(path and os.path.exists(path)), "file_exists"
        if kind == "result_field_equals":
            source = str(gate.get("source") or "director_result").strip()
            field = str(gate.get("field") or "").strip()
            expected = gate.get("equals")
            payload = _read_source_value(context, source)
            if not field:
                return False, "result_field_equals"
            actual = payload.get(field)
            return actual == expected, "result_field_equals"
        if kind == "verify_command_success":
            command = str(gate.get("command") or "").strip()
            if not command:
                return False, "verify_command_success_missing_command"

            verify_runs = _resolve_verify_runs_bucket(context)
            allow_verify = _normalize_bool(
                context.get("allow_verify_commands"),
                default=True,
            )
            if not allow_verify:
                verify_runs.append(
                    {
                        "command": command,
                        "deferred": True,
                        "reason": str(context.get("verify_deferred_reason") or "deferred_by_context").strip(),
                    }
                )
                return True, "verify_command_deferred"

            timeout_seconds = 180
            try:
                timeout_seconds = int(gate.get("timeout_seconds", timeout_seconds))
            except (RuntimeError, ValueError):
                timeout_seconds = 180
            timeout_seconds = max(timeout_seconds, 10)

            expected_exit_codes = gate.get("expected_exit_codes")
            if isinstance(expected_exit_codes, list):
                expected_codes = set()
                for item in expected_exit_codes:
                    token = str(item).strip()
                    if not token:
                        continue
                    try:
                        expected_codes.add(int(token))
                    except (RuntimeError, ValueError):
                        continue
            else:
                expected_codes = {0}
            if not expected_codes:
                expected_codes = {0}

            working_dir_token = str(gate.get("working_dir") or "workspace").strip()
            resolved_working_dir = _resolve_evidence_path(
                working_dir_token,
                workspace_full=workspace_full,
                run_dir=run_dir,
                evidence_index=evidence_index,
            )
            if not resolved_working_dir:
                resolved_working_dir = workspace_full
            if os.path.isfile(resolved_working_dir):
                resolved_working_dir = os.path.dirname(resolved_working_dir)
            if not os.path.isdir(resolved_working_dir):
                verify_runs.append(
                    {
                        "command": command,
                        "working_dir": resolved_working_dir,
                        "deferred": False,
                        "exit_code": 1,
                        "stderr_tail": [f"working_dir_not_found: {resolved_working_dir}"],
                    }
                )
                return False, "verify_command_working_dir_not_found"

            record = _execute_verify_command(
                command=command,
                working_dir=resolved_working_dir,
                timeout_seconds=timeout_seconds,
            )
            verify_runs.append(record)
            return int(record.get("exit_code", -1)) in expected_codes, "verify_command_success"
        return False, f"unknown_gate:{kind}"

    return False, "invalid_gate"


def evaluate_qa_contract(
    *,
    contract: dict[str, Any],
    context: dict[str, Any],
    workspace_full: str,
    run_dir: str,
    ui_plugin_enabled: bool = False,
) -> dict[str, Any]:
    verify_runs = _resolve_verify_runs_bucket(context)
    normalized = normalize_qa_contract(contract, task=context.get("task"))
    plugin_hint = str(normalized.get("plugin_hint") or "").strip().lower()
    plugin = str(normalized.get("plugin") or plugin_hint or _DEFAULT_PLUGIN).strip().lower()
    coordination = (
        normalized.get("coordination")
        if isinstance(normalized.get("coordination"), dict)
        else _normalize_coordination_policy(None)
    )
    director_status = str(context.get("director_status") or "").strip().lower()
    if director_status == "needs_continue":
        return {
            "verdict": "CONTINUE",
            "failed_gates": [],
            "missing_evidence": [],
            "evidence_paths": [],
            "diagnostics": "director_needs_continue",
            "plugin": plugin,
            "plugin_hint": plugin_hint,
            "task_type": str(normalized.get("task_type") or ""),
            "source": str(normalized.get("source") or ""),
            "retry_policy": normalized.get("retry_policy")
            if isinstance(normalized.get("retry_policy"), dict)
            else {"max_director_retries": _DEFAULT_MAX_DIRECTOR_RETRIES},
            "coordination": coordination,
            "verify_runs": verify_runs,
        }
    if plugin != _DEFAULT_PLUGIN:
        # Graceful degradation: non-default plugin requested but unavailable
        # Log warning and continue with rules_v1 basic QA instead of failing
        plugin_hint = plugin
        plugin = _DEFAULT_PLUGIN

    task_type = str(normalized.get("task_type") or "").strip().lower()
    if _looks_like_ui_or_3d_task(task_type) and not ui_plugin_enabled:
        # Graceful degradation: UI tasks without UI plugin still get rules_v1 QA
        # The specialized UI checks are skipped, but basic gates still apply
        pass

    evidence_index = context.get("evidence_index")
    evidence_index = evidence_index if isinstance(evidence_index, dict) else {}
    missing_evidence: list[str] = []
    evidence_paths: list[str] = []
    for item in normalized.get("evidence_required", []):
        resolved = _resolve_evidence_path(
            str(item or ""),
            workspace_full=workspace_full,
            run_dir=run_dir,
            evidence_index=evidence_index,
        )
        if not resolved or not os.path.exists(resolved):
            missing_evidence.append(str(item or ""))
            continue
        evidence_paths.append(resolved)

    failed_gates: list[str] = []
    for index, gate in enumerate(normalized.get("hard_gates", []), start=1):
        passed, label = _evaluate_gate(
            gate,
            context=context,
            workspace_full=workspace_full,
            run_dir=run_dir,
        )
        if not passed:
            failed_gates.append(f"hard[{index}]:{label}")

    for index, gate in enumerate(normalized.get("regression_gates", []), start=1):
        passed, label = _evaluate_gate(
            gate,
            context=context,
            workspace_full=workspace_full,
            run_dir=run_dir,
        )
        if not passed:
            failed_gates.append(f"regression[{index}]:{label}")

    verdict = "PASS"
    diagnostics = "qa_contract_passed"
    if missing_evidence:
        verdict = "INCONCLUSIVE"
        diagnostics = "missing_evidence"
    elif failed_gates:
        verdict = "FAIL"
        diagnostics = "gates_failed"

    if verdict not in _ALLOWED_VERDICTS:
        verdict = "INCONCLUSIVE"
        diagnostics = "invalid_verdict"

    return {
        "verdict": verdict,
        "failed_gates": failed_gates,
        "missing_evidence": missing_evidence,
        "evidence_paths": evidence_paths,
        "diagnostics": diagnostics,
        "plugin": plugin,
        "plugin_hint": plugin_hint,
        "task_type": task_type,
        "source": str(normalized.get("source") or ""),
        "retry_policy": normalized.get("retry_policy")
        if isinstance(normalized.get("retry_policy"), dict)
        else {"max_director_retries": _DEFAULT_MAX_DIRECTOR_RETRIES},
        "coordination": coordination,
        "verify_runs": verify_runs,
    }


__all__ = [
    "evaluate_qa_contract",
    "normalize_qa_contract",
    "normalize_qa_mode",
]
