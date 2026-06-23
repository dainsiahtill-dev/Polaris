"""Run ledger primitives for factory pipeline evidence.

This module is the small control-plane foundation for moving factory runs from
role-local claims toward one append-only evidence stream. It deliberately keeps
effects explicit: callers create a ``JobToken``/event and then choose whether
to persist it through ``RunLedger``.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_string(item).replace("\\", "/").lstrip("/")
        if text and text not in seen:
            output.append(text)
            seen.add(text)
    return output


def _string_items(value: Any) -> list[str]:
    if isinstance(value, str):
        text = _clean_string(value)
        return [text] if text else []
    return _string_list(value)


def _lineage_entries(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    output: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            output.append({str(key): item[key] for key in sorted(item)})
            continue
        text = _clean_string(item)
        if text:
            output.append({"ref": text})
    return output


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _event_content_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in event.items() if key not in {"append_id", "content_id", "event_id", "recorded_at"}
    }


@dataclass(frozen=True)
class JobToken:
    """Immutable task fact carried across PM/CE/Director/QA projections."""

    schema_version: int
    token_id: str
    run_id: str
    factory_run_id: str
    project_id: str
    stage: str
    target_files: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    required_artifacts: list[str] = field(default_factory=list)
    gate_policy: dict[str, Any] = field(default_factory=dict)
    capability_audit: dict[str, Any] = field(default_factory=dict)
    parent_token_id: str = ""
    repair_lineage: list[dict[str, Any]] = field(default_factory=list)
    contract_hash: str = ""
    blueprint_hash: str = ""
    source: str = "factory.pipeline"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RunLedger:
    """Append-only JSONL ledger for factory pipeline evidence."""

    def __init__(self, workspace: Path, *, run_id: str) -> None:
        self.workspace = Path(workspace)
        safe_run_id = _safe_token(run_id or "unknown")
        self.path = self.workspace / "runtime" / "factory" / "ledger" / f"{safe_run_id}.ndjson"

    def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        payload.setdefault("schema_version", 1)
        payload.setdefault("content_id", stable_hash(_event_content_payload(payload)))
        payload.setdefault("event_id", payload["content_id"])
        recorded_at = datetime.now(timezone.utc).isoformat()
        payload.setdefault("recorded_at", recorded_at)
        payload.setdefault(
            "append_id",
            stable_hash(
                {
                    "content_id": payload["content_id"],
                    "ledger_path": str(self.path),
                    "recorded_at": payload["recorded_at"],
                }
            ),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(stable_json(payload) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return {"ledger_path": str(self.path), "event": payload}

    def read_events(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            for line in handle.read().splitlines():
                if not line.strip():
                    continue
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    events.append(parsed)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return events


def _safe_token(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "").strip())
    return cleaned.strip("-") or "unknown"


def _path_is_allowed(path: str, allowed_paths: list[str]) -> bool:
    normalized = _clean_string(path).replace("\\", "/").strip("/")
    if not normalized:
        return False
    for allowed in allowed_paths:
        scope = _clean_string(allowed).replace("\\", "/").strip("/")
        if not scope:
            continue
        if normalized == scope or normalized.startswith(scope.rstrip("/") + "/"):
            return True
    return False


def _repair_targets_from_lineage(repair_lineage: list[dict[str, Any]]) -> list[str]:
    targets: list[str] = []
    for entry in repair_lineage:
        targets.extend(_string_list(entry.get("target_files")))
        targets.extend(_string_list(entry.get("changed_files")))
    return _string_list(targets)


def _job_token_from_ledger_meta(value: dict[str, Any]) -> dict[str, Any]:
    direct = value.get("job_token")
    if isinstance(direct, dict):
        return direct
    ledger_event = value.get("ledger_event")
    if isinstance(ledger_event, dict):
        nested = ledger_event.get("job_token")
        if isinstance(nested, dict):
            return nested
    return {}


def _build_capability_audit(
    *,
    contract_sources: list[str],
    blueprint_sources: list[str],
    target_files: list[str],
    allowed_paths: list[str],
    required_artifacts: list[str],
    repair_targets: list[str],
    qa_expectations: list[str],
) -> dict[str, Any]:
    target_outside_scope = [path for path in target_files if not _path_is_allowed(path, allowed_paths)]
    repair_outside_scope = [path for path in repair_targets if not _path_is_allowed(path, allowed_paths)]
    artifact_outside_scope = [path for path in required_artifacts if not _path_is_allowed(path, allowed_paths)]
    issues: list[str] = []
    if not contract_sources:
        issues.append("missing_pm_contract_source")
    if not blueprint_sources:
        issues.append("missing_ce_blueprint_source")
    if not target_files:
        issues.append("missing_target_files")
    if not allowed_paths:
        issues.append("missing_allowed_paths")
    if target_outside_scope:
        issues.append("target_files_outside_allowed_paths")
    if repair_outside_scope:
        issues.append("repair_targets_outside_allowed_paths")
    if artifact_outside_scope:
        issues.append("required_artifacts_outside_allowed_paths")
    return {
        "ok": not issues,
        "issues": issues,
        "contract_sources": contract_sources,
        "blueprint_sources": blueprint_sources,
        "target_files": target_files,
        "allowed_paths": allowed_paths,
        "required_artifacts": required_artifacts,
        "repair_targets": repair_targets,
        "qa_expectations": qa_expectations,
        "drift": {
            "target_files_outside_allowed_paths": target_outside_scope,
            "repair_targets_outside_allowed_paths": repair_outside_scope,
            "required_artifacts_outside_allowed_paths": artifact_outside_scope,
        },
    }


def summarize_run_ledger_meta(value: Any) -> dict[str, Any]:
    """Return a fail-closed summary for persisted run ledger metadata."""

    if not isinstance(value, dict):
        return {
            "ok": False,
            "detail": "run ledger metadata missing",
            "missing": ["ledger_path", "content_id", "event_id", "append_id", "job_token_id"],
        }
    required = ("ledger_path", "content_id", "event_id", "append_id", "job_token_id")
    missing = [key for key in required if not _clean_string(value.get(key))]
    ledger_path = _clean_string(value.get("ledger_path"))
    if missing:
        return {
            "ok": False,
            "detail": f"run ledger metadata missing fields: {', '.join(missing)}",
            "ledger_path": ledger_path,
            "missing": missing,
        }
    ledger_file = Path(ledger_path)
    if not ledger_file.is_file():
        return {
            "ok": False,
            "detail": f"run ledger file missing: {ledger_path}",
            "ledger_path": ledger_path,
            "missing": ["ledger_file"],
        }
    job_token = _job_token_from_ledger_meta(value)
    capability_audit = job_token.get("capability_audit") if isinstance(job_token, dict) else {}
    if not isinstance(capability_audit, dict):
        return {
            "ok": False,
            "detail": "run ledger job token missing capability audit",
            "ledger_path": ledger_path,
            "missing": ["job_token.capability_audit"],
        }
    capability_issues = capability_audit.get("issues")
    if not bool(capability_audit.get("ok")):
        issue_list = [str(item) for item in capability_issues] if isinstance(capability_issues, list) else []
        return {
            "ok": False,
            "detail": "run ledger capability invalid: " + ", ".join(issue_list or ["unknown"]),
            "ledger_path": ledger_path,
            "missing": issue_list,
            "capability_audit": capability_audit,
        }
    return {
        "ok": True,
        "detail": f"run ledger event {value['content_id']} written",
        "ledger_path": ledger_path,
        "content_id": _clean_string(value.get("content_id")),
        "event_id": _clean_string(value.get("event_id")),
        "append_id": _clean_string(value.get("append_id")),
        "job_token_id": _clean_string(value.get("job_token_id")),
        "capability_audit": capability_audit,
        "missing": [],
    }


def build_job_token_from_record(
    record: dict[str, Any],
    *,
    run_id: str = "",
    project_id: str = "",
    stage: str = "real_run_gate",
) -> JobToken:
    """Build a canonical job token from the current bench/factory record."""

    raw_chain = record.get("chain")
    chain: dict[str, Any] = raw_chain if isinstance(raw_chain, dict) else {}
    raw_chain_results = record.get("chain_results")
    chain_results: dict[str, Any] = raw_chain_results if isinstance(raw_chain_results, dict) else {}
    raw_audit_bundle = chain.get("audit_bundle")
    audit_bundle: dict[str, Any] = raw_audit_bundle if isinstance(raw_audit_bundle, dict) else {}
    raw_record_artifacts = record.get("artifacts")
    record_artifacts: dict[str, Any] = raw_record_artifacts if isinstance(raw_record_artifacts, dict) else {}
    raw_audit_artifacts = audit_bundle.get("artifacts")
    audit_artifacts: dict[str, Any] = raw_audit_artifacts if isinstance(raw_audit_artifacts, dict) else {}
    target_files = _string_list(
        record.get("target_files") or record.get("declared_source_targets") or record.get("code_files")
    )
    if not target_files:
        target_files = _string_list(record.get("code_files"))
    allowed_paths = _string_list(
        record.get("allowed_paths")
        or record.get("scope_paths")
        or record.get("target_files")
        or record.get("declared_source_targets")
        or record.get("code_files")
    )
    required_artifacts = _string_list(record.get("required_artifacts") or record.get("code_files"))
    parent_token_id = _clean_string(
        record.get("parent_token_id") or record.get("previous_job_token_id") or record.get("repair_parent_token_id")
    )
    repair_lineage = _lineage_entries(record.get("repair_lineage"))
    quality_repair = record.get("factory_workspace_quality_repair")
    if isinstance(quality_repair, dict):
        repair_lineage.append(
            {
                "source": "factory_workspace_quality_repair",
                "run_id": _clean_string(quality_repair.get("run_id")),
                "changed_files": _string_list(quality_repair.get("changed_files")),
                "target_files": _string_list(quality_repair.get("target_files")),
            }
        )
    repair_targets = _repair_targets_from_lineage(repair_lineage)
    qa_expectations = (
        _string_items(record.get("qa_expectations"))
        or _string_items(record.get("acceptance_criteria"))
        or _string_items(record.get("acceptance"))
        or _string_items(record.get("qa_contract"))
    )
    gate_policy = {
        "stage": stage,
        "requires_physical_artifacts": True,
        "requires_real_entrypoint": stage == "real_run_gate",
        "requires_command_evidence": stage == "real_run_gate",
    }
    contract_sources: list[str] = []
    if chain_results.get("contract_goal") or record.get("contract_goal"):
        contract_sources.append("contract_goal")
    if record.get("brief") or record.get("project_brief"):
        contract_sources.append("project_brief")
    if target_files:
        contract_sources.append("target_files")
    blueprint_sources: list[str] = []
    blueprint_artifacts = _string_list(record_artifacts.get("blueprint") or audit_artifacts.get("blueprint"))
    if record.get("blueprint_id") or audit_bundle.get("blueprint_id"):
        blueprint_sources.append("blueprint_id")
    if record.get("blueprints") or audit_bundle.get("blueprints"):
        blueprint_sources.append("blueprints")
    if record.get("chief_engineer") or audit_bundle.get("chief_engineer"):
        blueprint_sources.append("chief_engineer")
    if blueprint_artifacts:
        blueprint_sources.append("artifacts.blueprint")
    capability_audit = _build_capability_audit(
        contract_sources=contract_sources,
        blueprint_sources=blueprint_sources,
        target_files=target_files,
        allowed_paths=allowed_paths,
        required_artifacts=required_artifacts,
        repair_targets=repair_targets,
        qa_expectations=qa_expectations,
    )
    contract_facts = {
        "contract_goal": chain_results.get("contract_goal") or record.get("contract_goal") or "",
        "project_brief": record.get("brief") or record.get("project_brief") or "",
        "target_files": target_files,
        "allowed_paths": allowed_paths,
        "qa_expectations": qa_expectations,
    }
    blueprint_facts = {
        "blueprint_id": record.get("blueprint_id") or audit_bundle.get("blueprint_id") or "",
        "blueprints": record.get("blueprints") or audit_bundle.get("blueprints") or [],
        "blueprint_artifacts": blueprint_artifacts,
        "chief_engineer": record.get("chief_engineer") or audit_bundle.get("chief_engineer") or {},
    }
    token_run_id = _clean_string(run_id or record.get("run_id"))
    token_factory_run_id = _clean_string(chain.get("run_id") or record.get("factory_run_id"))
    token_project_id = _clean_string(project_id or record.get("id") or record.get("project_id"))
    contract_hash = stable_hash(contract_facts)
    blueprint_hash = stable_hash(blueprint_facts)
    token_payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": token_run_id,
        "factory_run_id": token_factory_run_id,
        "project_id": token_project_id,
        "stage": stage,
        "target_files": target_files,
        "allowed_paths": allowed_paths,
        "required_artifacts": required_artifacts,
        "gate_policy": gate_policy,
        "capability_audit": capability_audit,
        "parent_token_id": parent_token_id,
        "repair_lineage": repair_lineage,
        "contract_hash": contract_hash,
        "blueprint_hash": blueprint_hash,
        "source": "factory.pipeline",
    }
    return JobToken(
        schema_version=1,
        token_id=stable_hash(token_payload),
        run_id=token_run_id,
        factory_run_id=token_factory_run_id,
        project_id=token_project_id,
        stage=stage,
        target_files=target_files,
        allowed_paths=allowed_paths,
        required_artifacts=required_artifacts,
        gate_policy=gate_policy,
        capability_audit=capability_audit,
        parent_token_id=parent_token_id,
        repair_lineage=repair_lineage,
        contract_hash=contract_hash,
        blueprint_hash=blueprint_hash,
        source="factory.pipeline",
    )


def build_gate_ledger_event(
    job_token: JobToken,
    gate: dict[str, Any],
    *,
    gate_name: str = "real_run_gate",
) -> dict[str, Any]:
    """Convert a gate result into a standard append-only ledger event."""

    raw_requirements = gate.get("requirements")
    requirements: dict[str, Any] = raw_requirements if isinstance(raw_requirements, dict) else {}
    raw_entrypoint = gate.get("entrypoint")
    entrypoint: dict[str, Any] = raw_entrypoint if isinstance(raw_entrypoint, dict) else {}
    raw_commands = gate.get("commands")
    commands: list[Any] = raw_commands if isinstance(raw_commands, list) else []
    total_command_count = int(gate.get("command_count_total") or len(commands))
    event = {
        "schema_version": 1,
        "event_type": "gate_evaluated",
        "stage": job_token.stage,
        "job_token": job_token.to_dict(),
        "gate": {
            "name": gate_name,
            "ok": bool(gate.get("ok")),
            "summary": _clean_string(gate.get("summary")),
            "failing_requirements": [
                name for name, item in requirements.items() if isinstance(item, dict) and not bool(item.get("ok"))
            ],
        },
        "physical_evidence": {
            "requirements": requirements,
            "entrypoint": entrypoint,
            "command_count": total_command_count,
            "sampled_command_count": len(commands),
            "commands_truncated": bool(gate.get("commands_truncated")),
            "commands": commands,
        },
    }
    event["content_id"] = stable_hash(_event_content_payload(event))
    event["event_id"] = event["content_id"]
    return event


def persist_real_run_gate_ledger(
    workspace: Path,
    record: dict[str, Any],
    gate: dict[str, Any],
    *,
    run_id: str = "",
    project_id: str = "",
    stage: str = "real_run_gate",
    gate_name: str = "real_run_gate",
) -> dict[str, Any]:
    """Persist a real-run gate event and return lightweight ledger metadata."""

    token = build_job_token_from_record(record, run_id=run_id, project_id=project_id, stage=stage)
    event = build_gate_ledger_event(token, gate, gate_name=gate_name)
    persisted = RunLedger(workspace, run_id=token.run_id or run_id or "unknown").append_event(event)
    persisted_event = persisted["event"]
    return {
        "ledger_path": persisted["ledger_path"],
        "event_id": persisted_event["event_id"],
        "content_id": persisted_event["content_id"],
        "append_id": persisted_event["append_id"],
        "job_token_id": token.token_id,
        "job_token": token.to_dict(),
        "ledger_event": persisted_event,
        "stage": token.stage,
        "gate": gate_name,
    }


def build_run_ledger_projection(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the canonical read model for ledger-backed UI/QA projections."""

    gates: list[dict[str, Any]] = []
    capability_issues: list[str] = []
    job_token_ids: list[str] = []
    latest_token: dict[str, Any] = {}
    command_count_total = 0
    sampled_command_count = 0
    truncated_command_events = 0
    for event in events:
        if not isinstance(event, dict) or event.get("event_type") != "gate_evaluated":
            continue
        raw_gate = event.get("gate")
        gate: dict[str, Any] = raw_gate if isinstance(raw_gate, dict) else {}
        raw_physical_evidence = event.get("physical_evidence")
        physical_evidence: dict[str, Any] = raw_physical_evidence if isinstance(raw_physical_evidence, dict) else {}
        raw_job_token = event.get("job_token")
        job_token: dict[str, Any] = raw_job_token if isinstance(raw_job_token, dict) else {}
        raw_capability_audit = job_token.get("capability_audit")
        capability_audit: dict[str, Any] = raw_capability_audit if isinstance(raw_capability_audit, dict) else {}
        issues = capability_audit.get("issues")
        if isinstance(issues, list):
            capability_issues.extend(str(item) for item in issues if str(item))
        token_id = _clean_string(job_token.get("token_id"))
        if token_id:
            job_token_ids.append(token_id)
            latest_token = job_token
        command_count_total += int(physical_evidence.get("command_count") or 0)
        sampled_command_count += int(physical_evidence.get("sampled_command_count") or 0)
        if physical_evidence.get("commands_truncated"):
            truncated_command_events += 1
        gates.append(
            {
                "name": _clean_string(gate.get("name")) or "unknown",
                "stage": _clean_string(event.get("stage")),
                "ok": bool(gate.get("ok")),
                "summary": _clean_string(gate.get("summary")),
                "content_id": _clean_string(event.get("content_id") or event.get("event_id")),
                "append_id": _clean_string(event.get("append_id")),
                "job_token_id": token_id,
                "capability_ok": bool(capability_audit.get("ok")),
                "capability_issues": list(issues) if isinstance(issues, list) else [],
            }
        )
    failed_gates = [gate for gate in gates if not gate["ok"]]
    capability_ok = bool(gates) and not capability_issues and all(gate["capability_ok"] for gate in gates)
    integrity_ok = bool(gates) and capability_ok
    outcome_ok = bool(gates) and not failed_gates
    projection_ok = integrity_ok and outcome_ok
    return {
        "schema_version": 1,
        "source": "run_ledger",
        "ok": projection_ok,
        "integrity_ok": integrity_ok,
        "outcome_ok": outcome_ok,
        "event_count": len(events),
        "gate_count": len(gates),
        "missing": [] if gates else ["gate_events"],
        "gates": gates,
        "failed_gates": failed_gates,
        "capability": {
            "ok": capability_ok,
            "issues": sorted(set(capability_issues)),
            "latest_token_id": _clean_string(latest_token.get("token_id")) if latest_token else "",
            "latest_contract_hash": _clean_string(latest_token.get("contract_hash")) if latest_token else "",
            "latest_blueprint_hash": _clean_string(latest_token.get("blueprint_hash")) if latest_token else "",
            "job_token_ids": list(dict.fromkeys(job_token_ids)),
        },
        "physical_evidence": {
            "command_count": command_count_total,
            "sampled_command_count": sampled_command_count,
            "truncated_command_events": truncated_command_events,
        },
    }


def summarize_run_ledger_projection(value: Any) -> dict[str, Any]:
    """Return the control-plane integrity status for a ledger projection."""

    if not isinstance(value, dict):
        return {
            "ok": False,
            "detail": "run ledger projection missing",
            "missing": ["run_ledger_projection"],
        }
    if value.get("source") != "run_ledger":
        return {
            "ok": False,
            "detail": "run ledger projection source mismatch",
            "missing": ["source"],
        }
    if int(value.get("gate_count") or 0) <= 0:
        return {
            "ok": False,
            "detail": "run ledger projection has no gate events",
            "missing": ["gate_events"],
        }
    capability = value.get("capability")
    capability_map = capability if isinstance(capability, dict) else {}
    if not bool(capability_map.get("ok")):
        issues = capability_map.get("issues")
        issue_list = [str(item) for item in issues] if isinstance(issues, list) else ["capability"]
        return {
            "ok": False,
            "detail": "run ledger projection capability invalid: " + ", ".join(issue_list),
            "missing": issue_list,
            "capability": capability_map,
        }
    failed_gates = value.get("failed_gates")
    failed_gate_count = len(failed_gates) if isinstance(failed_gates, list) else 0
    return {
        "ok": True,
        "detail": f"run ledger projection ready ({int(value.get('gate_count') or 0)} gate event(s))",
        "missing": [],
        "outcome_ok": bool(value.get("outcome_ok")),
        "failed_gate_count": failed_gate_count,
        "capability": capability_map,
    }


def load_run_ledger_projection(workspace: Path, *, run_id: str) -> dict[str, Any]:
    """Read a run ledger file and return the canonical projection."""

    return build_run_ledger_projection(RunLedger(workspace, run_id=run_id).read_events())
