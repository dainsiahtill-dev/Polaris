"""Run provenance bundle builder for the control-plane ledger."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from polaris.cells.control_plane.run_ledger.public.ledger import stable_hash

_MISSING_PREFIX = "missing:"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_values = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_values = list(value)
    else:
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        text = _clean_string(item)
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def _first_present(*values: Any, missing_key: str) -> str:
    for value in values:
        text = _clean_string(value)
        if text:
            return text
    return f"{_MISSING_PREFIX}{missing_key}"


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        dict_rows = [value]
        for nested in value.values():
            dict_rows.extend(_walk_dicts(nested))
        return dict_rows
    if isinstance(value, (list, tuple)):
        item_rows: list[dict[str, Any]] = []
        for item in value:
            item_rows.extend(_walk_dicts(item))
        return item_rows
    return []


def _collect_hashes_by_keys(value: Any, keys: set[str]) -> list[str]:
    hashes: list[str] = []
    for row in _walk_dicts(value):
        for key in keys:
            token = _clean_string(row.get(key))
            if token:
                hashes.append(token)
    return list(dict.fromkeys(hashes))


def _collect_evidence_refs(value: Any) -> list[str]:
    refs: list[str] = []
    for row in _walk_dicts(value):
        for key in ("context_snapshot_ref", "evidence_ref", "verifier_logs_ref", "ledger_path", "_ledger_path"):
            token = _clean_string(row.get(key))
            if token:
                refs.append(token)
        refs.extend(_string_list(row.get("evidence_refs")))
        refs.extend(_string_list(row.get("receipt_refs")))
    return list(dict.fromkeys(refs))


def _collect_final_provider_request_hashes(events: list[dict[str, Any]]) -> list[str]:
    hashes: list[str] = []
    for row in _walk_dicts(events):
        if row.get("schema_version") == "polaris.final_request_evidence_coverage.v1":
            token = _clean_string(row.get("request_hash"))
            if token:
                hashes.append(token)
        if row.get("schema_version") == "llm.final_request_context_audit.v1":
            coverage = _dict_value(row.get("final_request_evidence_coverage"))
            token = _clean_string(coverage.get("request_hash"))
            if token:
                hashes.append(token)
        if row.get("schema_version") == "llm.provider_request_snapshot.v1":
            coverage = _dict_value(row.get("final_request_evidence_coverage"))
            token = _clean_string(coverage.get("request_hash"))
            if token:
                hashes.append(token)
        direct = _clean_string(row.get("request_hash") or row.get("final_provider_request_hash"))
        if direct:
            hashes.append(direct)
        hashes.extend(_string_list(row.get("final_provider_request_hashes")))
    return list(dict.fromkeys(hashes))


def _collect_tool_receipt_hashes(events: list[dict[str, Any]]) -> list[str]:
    hashes: list[str] = []
    for event in events:
        event_type = _clean_string(event.get("event_type"))
        if event_type == "tool_receipt":
            hashes.append(_first_present(event.get("content_id"), event.get("event_id"), missing_key="tool_receipt"))
        for row in _walk_dicts(event.get("physical_evidence")):
            if "effect_receipt" in row:
                receipt = _dict_value(row.get("effect_receipt"))
                hashes.append(stable_hash(receipt))
            if "tool_receipts" in row:
                for receipt in _walk_dicts(row.get("tool_receipts")):
                    hashes.append(stable_hash(receipt))
            if "write_receipts" in row:
                for receipt in _walk_dicts(row.get("write_receipts")):
                    hashes.append(stable_hash(receipt))
    return list(dict.fromkeys(token for token in hashes if token))


def _collect_command_receipt_hashes(events: list[dict[str, Any]]) -> list[str]:
    hashes: list[str] = []
    for event in events:
        physical_evidence = _dict_value(event.get("physical_evidence"))
        commands = physical_evidence.get("commands")
        if isinstance(commands, list):
            for command in commands:
                if isinstance(command, dict):
                    hashes.append(stable_hash(command))
    return list(dict.fromkeys(hashes))


def _latest_job_token(events: list[dict[str, Any]]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for event in events:
        token = _dict_value(event.get("job_token"))
        if token:
            latest = token
    return latest


def _latest_gate(events: list[dict[str, Any]]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for event in events:
        gate = _dict_value(event.get("gate"))
        if gate:
            latest = gate
    return latest


def _status_from_projection(projection: dict[str, Any], events: list[dict[str, Any]]) -> str:
    if not events:
        return "blocked"
    if bool(projection.get("ok")):
        return "success"
    evidence_policy = _dict_value(projection.get("evidence_policy"))
    capability = _dict_value(projection.get("capability"))
    missing = evidence_policy.get("missing_required_modalities") or projection.get("missing")
    if missing or not bool(capability.get("ok", True)):
        return "blocked"
    failed_gates = projection.get("failed_gates")
    if isinstance(failed_gates, list) and failed_gates:
        return "failed"
    return "partial"


def _workflow_hash_from_final_request(events: list[dict[str, Any]], key: str) -> str:
    for row in reversed(_walk_dicts(events)):
        workflow_chain = _dict_value(row.get("workflow_chain"))
        token = _clean_string(workflow_chain.get(key))
        if token:
            return token
    return ""


def build_run_provenance_bundle(
    *,
    workspace: str,
    run_id: str,
    events: list[dict[str, Any]],
    projection: dict[str, Any],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a machine-readable provenance bundle for one run."""

    latest_token = _latest_job_token(events)
    latest_gate = _latest_gate(events)
    status = _status_from_projection(projection, events)
    context_hashes = {
        "pm_contract_hash": _workflow_hash_from_final_request(events, "pm_contract_hash"),
        "ce_blueprint_hash": _workflow_hash_from_final_request(events, "ce_blueprint_hash"),
        "handoff_decision_hash": _workflow_hash_from_final_request(events, "handoff_decision_hash"),
        "execution_envelope_hash": _workflow_hash_from_final_request(events, "execution_envelope_hash"),
    }
    bundle_without_id = {
        "schema_version": "polaris.run_provenance_bundle.v1",
        "run_id": _clean_string(run_id) or _clean_string(latest_token.get("run_id")) or "unknown",
        "task_id": _first_present(
            latest_token.get("task_id"),
            latest_token.get("project_id"),
            projection.get("task_id"),
            missing_key="task_id",
        ),
        "workspace": _clean_string(workspace),
        "trace_id": _first_present(
            *_collect_hashes_by_keys(events, {"trace_id"}),
            missing_key="trace_id",
        ),
        "commit": _first_present(
            *_collect_hashes_by_keys(events, {"commit", "git_commit", "head_commit"}),
            missing_key="commit",
        ),
        "status": status,
        "pm_contract_hash": _first_present(
            context_hashes["pm_contract_hash"],
            latest_token.get("contract_hash"),
            missing_key="pm_contract_hash",
        ),
        "ce_blueprint_hash": _first_present(
            context_hashes["ce_blueprint_hash"],
            latest_token.get("blueprint_hash"),
            missing_key="ce_blueprint_hash",
        ),
        "handoff_decision_hash": _first_present(
            context_hashes["handoff_decision_hash"],
            *_collect_hashes_by_keys(events, {"handoff_decision_hash", "ce_handoff_decision_hash"}),
            missing_key="handoff_decision_hash",
        ),
        "execution_envelope_hash": _first_present(
            context_hashes["execution_envelope_hash"],
            *_collect_hashes_by_keys(events, {"execution_envelope_hash"}),
            missing_key="execution_envelope_hash",
        ),
        "final_provider_request_hashes": _collect_final_provider_request_hashes(events),
        "tool_receipt_hashes": _collect_tool_receipt_hashes(events),
        "file_diff_hash": _first_present(
            *_collect_hashes_by_keys(events, {"file_diff_hash", "diff_hash"}),
            missing_key="file_diff_hash",
        ),
        "command_receipt_hashes": _collect_command_receipt_hashes(events),
        "qa_result_hash": _first_present(
            latest_gate.get("content_id"),
            *_collect_hashes_by_keys(events, {"qa_result_hash", "verifier_result_hash"}),
            missing_key="qa_result_hash",
        ),
        "verifier_logs_ref": _first_present(
            *_collect_hashes_by_keys(events, {"verifier_logs_ref", "verifier_log_ref"}),
            missing_key="verifier_logs_ref",
        ),
        "final_status": _clean_string(projection.get("status")) or status,
        "evidence_refs": _collect_evidence_refs(events),
        "created_at": created_at or _utc_now(),
    }
    bundle_id = "run-prov-" + stable_hash(bundle_without_id)[:24]
    return {"bundle_id": bundle_id, **bundle_without_id}


__all__ = ["build_run_provenance_bundle"]
