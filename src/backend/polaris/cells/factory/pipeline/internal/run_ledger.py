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
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                events.append(parsed)
        return events


def _safe_token(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "").strip())
    return cleaned.strip("-") or "unknown"


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
    gate_policy = {
        "stage": stage,
        "requires_physical_artifacts": True,
        "requires_real_entrypoint": stage == "real_run_gate",
        "requires_command_evidence": stage == "real_run_gate",
    }
    contract_facts = {
        "contract_goal": chain_results.get("contract_goal") or record.get("contract_goal") or "",
        "project_brief": record.get("brief") or record.get("project_brief") or "",
        "target_files": target_files,
        "allowed_paths": allowed_paths,
    }
    blueprint_facts = {
        "blueprint_id": record.get("blueprint_id") or audit_bundle.get("blueprint_id") or "",
        "blueprints": record.get("blueprints") or audit_bundle.get("blueprints") or [],
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
