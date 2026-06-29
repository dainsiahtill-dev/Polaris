"""CE consumer that polls PENDING_DESIGN and generates blueprints."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from polaris.cells.chief_engineer.blueprint.internal.adr_store import ADRStore
from polaris.cells.chief_engineer.blueprint.internal.architecture_decisions import (
    infer_architecture_decisions,
    merge_architecture_decisions,
    normalize_architecture_decisions,
    selected_libraries_from_decisions,
)
from polaris.cells.chief_engineer.blueprint.internal.blueprint_persistence import (
    BlueprintPersistence,
)
from polaris.cells.chief_engineer.blueprint.internal.chief_engineer_preflight import (
    PreflightContext,
    run_pre_dispatch_chief_engineer_ctx,
)
from polaris.cells.chief_engineer.blueprint.internal.handoff import (
    build_handoff_decision,
    handoff_enforcement_enabled,
)
from polaris.cells.chief_engineer.blueprint.internal.step_contract import (
    build_blueprint_tasks_contract,
    normalize_construction_step,
    validate_construction_steps,
)
from polaris.cells.control_plane.run_ledger.public import JobToken, stable_hash
from polaris.cells.control_plane.verifier_policy.public import (
    CompileEvidencePolicyCommandV1,
    compile_evidence_policy,
)
from polaris.cells.director.tasking.public.service import (
    build_director_execution_profile_snapshot,
)
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
    PublishTaskWorkItemCommandV1,
)
from polaris.cells.runtime.task_market.public.service import get_task_market_service
from polaris.kernelone.quality.file_ownership_ledger import (
    read_file_owners,
    record_file_owners,
    render_edit_contract,
)
from polaris.kernelone.quality.interface_ledger import (
    read_declared_interfaces,
    record_declared_interfaces,
    render_assume_contract,
)

logger = logging.getLogger(__name__)

_DEFAULT_CE_FISSION_MAX_OUTPUT_TOKENS = 128_000


def _ce_fission_max_output_tokens() -> int:
    """Output-token budget for the CE step-fission LLM call (I3-r17).

    The shared role-caller default is 4000, which is structurally below the
    reasoning a model such as MiniMax-M3 burns before emitting the JSON answer
    (live r17: ~9.7k thinking tokens, finish_reason=length, empty content). The
    engine clamps the request to the model's max_output_tokens, so this only
    raises the floor where the model allows; the provider self-heal then carries
    it the rest of the way. Env-tunable, never a hardcoded project value.
    """
    raw = os.getenv("KERNELONE_CE_FISSION_MAX_TOKENS", "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_CE_FISSION_MAX_OUTPUT_TOKENS
    return value if value > 0 else _DEFAULT_CE_FISSION_MAX_OUTPUT_TOKENS


def _normalize_owned_target(raw: Any) -> str:
    """Match file_ownership_ledger._normalize_target so publish-time lookups hit."""
    target = str(raw or "").strip().replace("\\", "/")
    while target.startswith("./"):
        target = target[2:]
    return target


def _blueprint_runtime_path(blueprint_id: str) -> str:
    return f"runtime/blueprints/{blueprint_id}.json"


_CONTROL_PLANE_SELF_REF_KEYS = frozenset(
    {
        "blueprint_hash",
        "job_token",
        "control_plane_job_token",
        "capability_token",
        "capability_token_hash",
        "job_token_id",
    }
)


def _hashable_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _hashable_payload(item)
            for key, item in value.items()
            if str(key) not in _CONTROL_PLANE_SELF_REF_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_hashable_payload(item) for item in value]
    return value


def _payload_hash(value: Any) -> str:
    return stable_hash(_hashable_payload(value))


def _default_gate_policy() -> dict[str, Any]:
    return {
        "source": "chief_engineer.default_gate_policy",
        "enabled_evidence_modalities": ["tool_receipt", "build_test", "entry_smoke"],
        "required_evidence_modalities": ["tool_receipt"],
    }


def _compiled_gate_policy(
    *,
    workspace: str,
    task_id: str,
    run_id: str,
    target_files: list[str],
    acceptance_criteria: list[str],
    project_type: str,
    language: str,
) -> dict[str, Any]:
    default_policy = _default_gate_policy()
    try:
        compiled_policy = compile_evidence_policy(
            CompileEvidencePolicyCommandV1(
                workspace=workspace,
                task_id=task_id,
                run_id=run_id,
                project_type=project_type,
                language=language,
                target_files=tuple(target_files),
                acceptance_criteria=tuple(acceptance_criteria),
            )
        ).policy
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("CE evidence policy compilation failed for task %s: %s", task_id, exc)
        return {
            **default_policy,
            "evidence_policy_compiler": {
                "ok": False,
                "error": str(exc),
            },
        }
    compiled_gate = compiled_policy.get("gate_policy")
    gate_policy = dict(compiled_gate) if isinstance(compiled_gate, dict) else {}
    enabled = list(
        dict.fromkeys(
            [
                *default_policy["enabled_evidence_modalities"],
                *_string_list(gate_policy.get("enabled_evidence_modalities")),
            ]
        )
    )
    required = list(
        dict.fromkeys(
            [
                *default_policy["required_evidence_modalities"],
                *_string_list(gate_policy.get("required_evidence_modalities")),
            ]
        )
    )
    return {
        **default_policy,
        **gate_policy,
        "enabled_evidence_modalities": enabled,
        "required_evidence_modalities": required,
        "advisory_modalities": _string_list(gate_policy.get("advisory_modalities")),
        "waived_modalities": list(compiled_policy.get("waived_modalities") or []),
        "unavailable_required_blockers": list(compiled_policy.get("unavailable_required_blockers") or []),
        "compiled_evidence_policy_hash": str(compiled_policy.get("policy_hash") or ""),
        "evidence_policy_compiler": {
            "ok": True,
            "policy_hash": str(compiled_policy.get("policy_hash") or ""),
            "project_profile": str(compiled_policy.get("project_profile") or ""),
            "source": str(compiled_policy.get("source") or ""),
        },
    }


def _control_plane_job_token(
    *,
    workspace: str,
    task_id: str,
    payload: dict[str, Any],
    blueprint_id: str,
    blueprint_path: str,
    blueprint_hash: str,
    contract_hash: str,
    target_files: list[str],
    scope_paths: list[str],
    acceptance_criteria: list[str],
    project_type: str,
    language: str,
) -> dict[str, Any]:
    run_id = str(payload.get("run_id") or "").strip()
    project_id = (
        str(payload.get("project_id") or payload.get("plan_id") or "").strip()
        or str(payload.get("root_task_id") or "").strip()
        or task_id
    )
    token_basis = {
        "schema_version": 1,
        "run_id": run_id,
        "project_id": project_id,
        "task_id": task_id,
        "blueprint_id": blueprint_id,
        "blueprint_hash": blueprint_hash,
        "contract_hash": contract_hash,
        "target_files": target_files,
        "scope_paths": scope_paths,
    }
    token_id = f"job-{stable_hash(token_basis)[:24]}"
    allowed_paths = list(dict.fromkeys([*target_files, *scope_paths]))
    missing_issues = [
        issue
        for issue, missing in (
            ("missing_contract_hash", not contract_hash),
            ("missing_blueprint_hash", not blueprint_hash),
            ("missing_allowed_paths", not allowed_paths),
        )
        if missing
    ]
    gate_policy = _compiled_gate_policy(
        workspace=workspace,
        task_id=task_id,
        run_id=run_id,
        target_files=target_files,
        acceptance_criteria=acceptance_criteria,
        project_type=project_type,
        language=language,
    )
    return JobToken(
        schema_version=1,
        token_id=token_id,
        run_id=run_id,
        factory_run_id=str(payload.get("factory_run_id") or run_id).strip(),
        project_id=project_id,
        stage="pending_exec",
        target_files=list(target_files),
        allowed_paths=allowed_paths,
        required_artifacts=[blueprint_path, *target_files],
        gate_policy=gate_policy,
        capability_audit={"ok": not missing_issues, "issues": missing_issues},
        contract_hash=contract_hash,
        blueprint_hash=blueprint_hash,
    ).to_dict()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        token = value.strip()
        return [token] if token else []
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    rows: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            token = str(
                item.get("path")
                or item.get("file")
                or item.get("description")
                or item.get("text")
                or item.get("title")
                or item.get("name")
                or item.get("id")
                or item.get("value")
                or ""
            ).strip()
        else:
            token = str(item or "").strip()
        if token and token not in seen:
            seen.add(token)
            rows.append(token)
    return rows


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _first_string_list(*values: Any) -> list[str]:
    for value in values:
        rows = _string_list(value)
        if rows:
            return rows
    return []


def _merge_string_lists(*values: Any) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in _string_list(value):
            token = str(item or "").strip()
            key = token.casefold()
            if not token or key in seen:
                continue
            seen.add(key)
            rows.append(token)
    return rows


def _payload_task(payload: dict[str, Any]) -> dict[str, Any]:
    return _mapping(payload.get("pm_contract")) or _mapping(payload.get("task"))


def _scope_paths_from_payload(payload: dict[str, Any], blueprint_result: dict[str, Any] | None = None) -> list[str]:
    task = _payload_task(payload)
    blueprint = blueprint_result or {}
    return _merge_string_lists(
        blueprint.get("scope_paths"),
        payload.get("scope_paths"),
        task.get("scope_paths"),
        blueprint.get("scope"),
        payload.get("scope"),
        task.get("scope"),
    )


def _target_files_from_payload(
    payload: dict[str, Any],
    blueprint_result: dict[str, Any] | None = None,
    *,
    scope_paths: list[str] | None = None,
) -> list[str]:
    task = _payload_task(payload)
    blueprint = blueprint_result or {}
    target_like = _merge_string_lists(
        blueprint.get("target_files"),
        payload.get("target_files"),
        task.get("target_files"),
        blueprint.get("files"),
        payload.get("files"),
        task.get("files"),
        blueprint.get("affected_files"),
        payload.get("affected_files"),
        task.get("affected_files"),
    )
    if target_like:
        return target_like
    return _merge_string_lists(
        scope_paths or [], blueprint.get("scope_paths"), payload.get("scope_paths"), task.get("scope_paths")
    )


def _contract_fields(payload: dict[str, Any]) -> dict[str, Any]:
    task = _payload_task(payload)
    qa_contract = _mapping(task.get("qa_contract")) or _mapping(payload.get("qa_contract"))
    acceptance_criteria = _first_string_list(
        payload.get("acceptance_criteria"),
        payload.get("acceptance"),
        task.get("acceptance_criteria"),
        task.get("acceptance"),
        qa_contract.get("acceptance_criteria"),
        qa_contract.get("acceptance"),
    )
    execution_checklist = _first_string_list(
        payload.get("execution_checklist"),
        payload.get("steps"),
        task.get("execution_checklist"),
        task.get("steps"),
    )
    dependencies = _first_string_list(
        payload.get("dependencies"),
        payload.get("depends_on"),
        payload.get("blocked_by"),
        task.get("dependencies"),
        task.get("depends_on"),
        task.get("blocked_by"),
    )
    constraints = _mapping(payload.get("constraints")) or _mapping(task.get("constraints"))
    risks = _first_string_list(
        payload.get("risks"),
        payload.get("risk_flags"),
        task.get("risks"),
        task.get("risk_flags"),
    )
    architecture_decisions = normalize_architecture_decisions(
        payload.get("architecture_decisions")
        or payload.get("architectureDecision")
        or task.get("architecture_decisions")
        or task.get("architectureDecision")
    )
    return {
        "task": task,
        "qa_contract": qa_contract,
        "acceptance_criteria": acceptance_criteria,
        "execution_checklist": execution_checklist,
        "dependencies": dependencies,
        "constraints": constraints,
        "risks": risks,
        "architecture_decisions": architecture_decisions,
    }


def _contract_completeness(
    *,
    target_files: list[str],
    acceptance_criteria: list[str],
    execution_checklist: list[str],
) -> dict[str, Any]:
    missing_fields: list[str] = []
    if not target_files:
        missing_fields.append("target_files")
    if not acceptance_criteria:
        missing_fields.append("acceptance_criteria")
    if not execution_checklist:
        missing_fields.append("execution_checklist")
    return {
        "handoff_ready": not missing_fields,
        "missing_fields": missing_fields,
        "requires": ["target_files", "acceptance_criteria", "execution_checklist"],
    }


class CEConsumer:
    """ChiefEngineer consumer for PENDING_DESIGN tasks.

    This consumer polls the task market for tasks in the ``pending_design`` stage,
    runs the CE preflight to generate a blueprint, and acknowledges the task with
    ``pending_exec`` as the next stage.

    Args:
        workspace: Workspace path for task market operations.
        worker_id: Unique identifier for this worker instance.
        visibility_timeout_seconds: How long a claimed task is locked before it
            becomes visible to other workers again on failure.
        poll_interval: Deprecated compatibility argument; consumers now wait on
            task-market wake signals when no task is found.
        enable_director_pool: Legacy flag for ADRStore-backed blueprint persistence.
    """

    def __init__(
        self,
        workspace: str,
        worker_id: str = "ce_worker",
        analysis_runner: Any | None = None,
        visibility_timeout_seconds: int = 900,
        poll_interval: float = 5.0,
        enable_director_pool: bool = True,
        wake_event: threading.Event | None = None,
    ) -> None:
        self._workspace = str(workspace or "").strip()
        if not self._workspace:
            raise ValueError("workspace must be a non-empty string")
        self._worker_id = str(worker_id or "").strip()
        if not self._worker_id:
            raise ValueError("worker_id must be a non-empty string")
        self._visibility_timeout = int(visibility_timeout_seconds)
        # Injected by the host/driver layer (cells never import delivery).
        self._analysis_runner = analysis_runner
        self._stop_event = threading.Event()
        self._work_event = wake_event or threading.Event()
        self._svc = get_task_market_service()
        self._enable_director_pool = bool(enable_director_pool)
        self._adr_store: ADRStore | None = None
        if self._enable_director_pool:
            self._adr_store = ADRStore(workspace=self._workspace)

    def poll_once(self) -> list[dict[str, Any]]:
        """Poll once for PENDING_DESIGN tasks.

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

    def _claim_and_process_one(self) -> dict[str, Any] | None:
        """Attempt to claim one PENDING_DESIGN task and process it.

        Returns:
            Processed result dict, or None if no claimable task was found.
        """
        claim = self._svc.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=self._workspace,
                stage="pending_design",
                worker_id=self._worker_id,
                worker_role="chief_engineer",
                visibility_timeout_seconds=self._visibility_timeout,
            )
        )
        if not claim.ok:
            return None

        task_id = str(claim.task_id or "").strip()
        lease_token = str(claim.lease_token or "").strip()

        try:
            payload: dict[str, Any] = dict(claim.payload) if claim.payload else {}
            blueprint_result = self._run_ce_preflight(task_id, payload)

            blueprint_id = str(blueprint_result.get("blueprint_id", f"bp-{task_id}"))
            scope_paths = _scope_paths_from_payload(payload, blueprint_result)
            target_files = _target_files_from_payload(payload, blueprint_result, scope_paths=scope_paths)
            contract = _contract_fields(payload)
            acceptance_criteria = list(contract["acceptance_criteria"])
            execution_checklist = list(contract["execution_checklist"])
            dependencies = list(contract["dependencies"])
            constraints = dict(contract["constraints"])
            risks = list(contract["risks"])
            explicit_decisions = merge_architecture_decisions(
                tuple(contract["architecture_decisions"]),
                normalize_architecture_decisions(blueprint_result.get("architecture_decisions")),
            )
            objective = str(payload.get("objective") or payload.get("description") or payload.get("title") or task_id)
            architecture_decisions = merge_architecture_decisions(
                explicit_decisions,
                infer_architecture_decisions(
                    objective=objective,
                    context=payload,
                    constraints=constraints,
                    target_files=target_files,
                    scope_paths=scope_paths,
                    dependencies=dependencies,
                ),
            )
            architecture_decision_payloads = [decision.to_dict() for decision in architecture_decisions]
            selected_libraries = list(selected_libraries_from_decisions(architecture_decisions))
            contract_completeness = _contract_completeness(
                target_files=target_files,
                acceptance_criteria=acceptance_criteria,
                execution_checklist=execution_checklist,
            )
            blueprint_path = _blueprint_runtime_path(blueprint_id)
            contract_hash = str(payload.get("contract_hash") or payload.get("pm_contract_hash") or "").strip()
            if not contract_hash:
                contract_hash = _payload_hash(dict(contract["task"]))
            project_metadata = _mapping(payload.get("project_metadata"))
            task_metadata = _mapping(_mapping(contract["task"]).get("metadata"))
            project_type = str(
                payload.get("project_type")
                or project_metadata.get("project_type")
                or task_metadata.get("project_type")
                or ""
            )
            language = str(
                payload.get("language")
                or payload.get("main_language")
                or project_metadata.get("language")
                or project_metadata.get("main_language")
                or task_metadata.get("language")
                or ""
            )
            profile_metadata = {
                **payload,
                "contract_hash": contract_hash,
                "pm_contract_hash": contract_hash,
                "target_files": target_files,
                "scope_paths": scope_paths,
                "acceptance_criteria": acceptance_criteria,
                "execution_checklist": execution_checklist,
                "project_type": project_type,
                "language": language,
            }
            profile_snapshot = build_director_execution_profile_snapshot(
                subject=objective,
                description=str(
                    payload.get("description")
                    or payload.get("goal")
                    or payload.get("summary")
                    or blueprint_result.get("description")
                    or ""
                ),
                metadata=profile_metadata,
                target_files=target_files,
                scope_paths=scope_paths,
                workspace=self._workspace,
            )
            director_execution_profile = dict(profile_snapshot["profile"])
            execution_profile_hash = str(profile_snapshot["profile_hash"])
            execution_profile_ref = str(profile_snapshot["profile_ref"])
            blueprint_record: dict[str, Any] = {
                "schema_version": "chief_engineer.blueprint.v1",
                "blueprint_id": blueprint_id,
                "status": "approved",
                "task_id": task_id,
                "run_id": str(payload.get("run_id", "")),
                "route": "chief_blueprint_required",
                "preflight_result": blueprint_result,
                "scope_paths": scope_paths,
                "target_files": target_files,
                "acceptance_criteria": acceptance_criteria,
                "execution_checklist": execution_checklist,
                "dependencies": dependencies,
                "architecture_decisions": architecture_decision_payloads,
                "selected_libraries": selected_libraries,
                "constraints": constraints,
                "risks": risks,
                "pm_task": dict(contract["task"]),
                "pm_contract_hash": contract_hash,
                "contract_hash": contract_hash,
                "qa_contract": dict(contract["qa_contract"]),
                "director_execution_profile": director_execution_profile,
                "task_execution_profile": director_execution_profile,
                "execution_profile_ref": execution_profile_ref,
                "execution_profile_hash": execution_profile_hash,
                "director_execution_profile_hash": execution_profile_hash,
                "task_execution_profile_hash": execution_profile_hash,
                "contract_completeness": contract_completeness,
                "handoff_ready": bool(contract_completeness["handoff_ready"]),
                "guardrails": blueprint_result.get("guardrails", []),
                "no_touch_zones": blueprint_result.get("no_touch_zones", []),
            }
            blueprint_hash = _payload_hash(blueprint_record)
            blueprint_record["blueprint_hash"] = blueprint_hash
            job_token = _control_plane_job_token(
                workspace=self._workspace,
                task_id=task_id,
                payload=payload,
                blueprint_id=blueprint_id,
                blueprint_path=blueprint_path,
                blueprint_hash=blueprint_hash,
                contract_hash=contract_hash,
                target_files=target_files,
                scope_paths=scope_paths,
                acceptance_criteria=acceptance_criteria,
                project_type=project_type,
                language=language,
            )
            control_plane_lineage = {
                "source": "chief_engineer.handoff",
                "job_token_id": str(job_token.get("token_id") or ""),
                "contract_hash": contract_hash,
                "blueprint_hash": blueprint_hash,
                "execution_profile_ref": execution_profile_ref,
                "execution_profile_hash": execution_profile_hash,
                "director_execution_profile_hash": execution_profile_hash,
                "blueprint_id": blueprint_id,
                "blueprint_path": blueprint_path,
            }
            ack_payload: dict[str, Any] = {
                "blueprint_id": blueprint_id,
                "blueprint_path": blueprint_path,
                "runtime_blueprint_path": blueprint_path,
                "blueprint_hash": blueprint_hash,
                "contract_hash": contract_hash,
                "pm_contract_hash": contract_hash,
                "execution_profile_ref": execution_profile_ref,
                "task_execution_profile_ref": execution_profile_ref,
                "director_execution_profile_ref": execution_profile_ref,
                "execution_profile_hash": execution_profile_hash,
                "task_execution_profile_hash": execution_profile_hash,
                "director_execution_profile_hash": execution_profile_hash,
                "job_token_id": str(job_token.get("token_id") or ""),
                "job_token": job_token,
                "control_plane_job_token": job_token,
                "capability_token": job_token,
                "control_plane_lineage": control_plane_lineage,
                "context_snapshot_ref": str(payload.get("context_snapshot_ref", "")),
                "guardrails": blueprint_result.get("guardrails", []),
                "no_touch_zones": blueprint_result.get("no_touch_zones", []),
                "scope_paths": scope_paths,
                "target_files": target_files,
                "acceptance_criteria": acceptance_criteria,
                "execution_checklist": execution_checklist,
                "dependencies": dependencies,
                "architecture_decisions": architecture_decision_payloads,
                "selected_libraries": selected_libraries,
                "constraints": constraints,
                "risks": risks,
                "pm_contract": dict(contract["task"]),
                "qa_contract": dict(contract["qa_contract"]),
                "director_execution_profile": director_execution_profile,
                "task_execution_profile": director_execution_profile,
                "contract_completeness": contract_completeness,
                "handoff_ready": bool(contract_completeness["handoff_ready"]),
                "route": "chief_blueprint_required",
                "task_market_route": "chief_blueprint_required",
                "blueprint_required": True,
            }

            if self._enable_director_pool and self._adr_store is not None:
                self._adr_store.create_blueprint(
                    blueprint_id,
                    blueprint_record,
                )
                self._adr_store.compile(blueprint_id)

                ack_payload["director_pool_assignment"] = "deferred_to_task_market"
            else:
                BlueprintPersistence(self._workspace).save(blueprint_id, blueprint_record)

            fission_published = 0
            if self._step_fission_enabled():
                steps, gate_errors = self._run_step_fission(task_id, payload, blueprint_id=blueprint_id)
                if gate_errors:
                    # CE-stage circuit breaker: junk steps never reach the market.
                    self._svc.fail_task_stage(
                        FailTaskStageCommandV1(
                            workspace=self._workspace,
                            task_id=task_id,
                            lease_token=lease_token,
                            error_code="CE_step_gate_failed",
                            error_message="; ".join(gate_errors[:6]),
                            requeue_stage="pending_design",
                        )
                    )
                    return {"task_id": task_id, "ok": False, "reason": "CE_step_gate_failed"}
                # Freeze this parent's declared interfaces so sibling parents
                # that share a file reuse the exact names (组合律 ledger).
                _cache_root = str(payload.get("cache_root", ""))
                record_declared_interfaces(self._workspace, _cache_root, steps)
                # Cross-parent file ownership (one file = one owner, I3-r18): read
                # which of this parent's target_files are ALREADY owned by an
                # earlier parent (BEFORE recording), then claim the rest for this
                # parent. The publish step then serializes a later writer AFTER its
                # owner so the second writer EDITS rather than clobbers.
                prior_file_owners = read_file_owners(
                    self._workspace,
                    _cache_root,
                    [str(s.get("target_file") or "") for s in steps],
                )
                # Right-size (I3-r29): split an over-budget from-scratch code step into
                # a skeleton + incremental fill chain so the bounded-window weak Director
                # never one-shots a too-large file (live: 12-function main.js truncated →
                # never materialized → dead_letter). Cross-parent edit targets (already
                # owned by an earlier parent) are skipped — they are small edits, not
                # creations. Adopt only a gate-clean split (fail-open to the original).
                from polaris.cells.chief_engineer.blueprint.internal.step_splitter import (
                    split_oversize_steps,
                )

                split_steps = split_oversize_steps(
                    steps, parent_pm_task=task_id, owned_elsewhere=set(prior_file_owners)
                )
                if split_steps is not steps:
                    split_errors = validate_construction_steps(split_steps, parent_pm_task=task_id)
                    if split_errors:
                        logger.warning(
                            "step split re-gate failed for %s, keeping original steps: %s",
                            task_id,
                            "; ".join(split_errors[:3]),
                        )
                    else:
                        steps = split_steps
                record_file_owners(self._workspace, _cache_root, steps, task_id)
                steps_contract = build_blueprint_tasks_contract(
                    parent_pm_task=task_id,
                    blueprint_id=blueprint_id,
                    blueprint_path=blueprint_path,
                    steps=steps,
                )
                from polaris.kernelone.fs.text_ops import write_json_atomic
                from polaris.kernelone.storage.io_paths import resolve_artifact_path

                write_json_atomic(
                    resolve_artifact_path(
                        self._workspace,
                        str(payload.get("cache_root", "")),
                        "runtime/contracts/ce_blueprint_tasks.contract.json",
                    ),
                    steps_contract,
                )
                fission_published = self._publish_step_tasks(
                    task_id,
                    {**payload, **ack_payload},
                    steps,
                    blueprint_id=blueprint_id,
                    blueprint_path=blueprint_path,
                    prior_file_owners=prior_file_owners,
                )
                ack_payload["construction_steps"] = steps
                ack_payload["fission_step_count"] = fission_published
                # The parent becomes a non-leaf supervision row; Director
                # workers must claim only leaf steps (I2: leaf-only claim gate).
                ack_payload["is_leaf"] = False

            # Director-handoff gate (Tier-2): evaluate the quality gate +
            # open blocker/critical risks for this task. The decision is
            # always surfaced on the ack payload; enforcement (requeue
            # instead of handoff) is opt-in via KERNELONE_CE_HANDOFF_ENFORCEMENT
            # (default OFF — pipeline behavior is unchanged until enabled).
            handoff_decision = build_handoff_decision(
                self._workspace,
                blueprint=ack_payload,
                blueprint_id=blueprint_id,
                task_id=task_id,
            )
            ack_payload["handoff_decision"] = handoff_decision.to_dict()
            if handoff_enforcement_enabled() and not handoff_decision.allowed:
                # Requeue to pending_design. CE_quality_gate_blocked is a
                # consuming requeue (not in _NON_CONSUMING_REQUEUE_ERROR_CODES),
                # so a blueprint that stays blocked eventually dead-letters
                # rather than looping forever — intentional fail-closed terminus.
                self._svc.fail_task_stage(
                    FailTaskStageCommandV1(
                        workspace=self._workspace,
                        task_id=task_id,
                        lease_token=lease_token,
                        error_code="CE_quality_gate_blocked",
                        error_message=handoff_decision.reason,
                        requeue_stage="pending_design",
                    )
                )
                return {
                    "task_id": task_id,
                    "ok": False,
                    "reason": "CE_quality_gate_blocked",
                    "handoff_decision": handoff_decision.to_dict(),
                }

            ack = self._svc.acknowledge_task_stage(
                AcknowledgeTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    next_stage="pending_exec",
                    summary=(
                        f"Blueprint {ack_payload['blueprint_id']} fissioned into {fission_published} step task(s)"
                        if fission_published
                        else f"Blueprint {ack_payload['blueprint_id']} ready for Director"
                    ),
                    metadata=ack_payload,
                )
            )
            return {
                "task_id": task_id,
                "ok": bool(ack.ok),
                "status": str(ack.status or ""),
                "fission_step_count": fission_published,
            }

        except Exception as exc:
            logger.exception("CE consumer failed for task %s: %s", task_id, exc)
            self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="CE_design_failed",
                    error_message=str(exc),
                    requeue_stage="pending_design",
                )
            )
            return {
                "task_id": task_id,
                "ok": False,
                "reason": str(exc),
            }

    @staticmethod
    def _step_fission_enabled() -> bool:
        """Three-tier fission flag (migration default OFF; I3 comparison flips it)."""
        return os.environ.get("KERNELONE_CE_STEP_FISSION", "0").strip().lower() in {"1", "true", "on", "yes"}

    def _run_step_fission(
        self,
        task_id: str,
        payload: dict[str, Any],
        *,
        blueprint_id: str,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Ask the CE role (cloud model) to fission one PM task into steps.

        Returns (steps, gate_errors). Steps are normalized; gate_errors non-empty
        means the CE-stage circuit breaker fired (junk steps never reach the
        market — a malformed step burns ~10min of local Director wall clock).
        """
        import asyncio
        import json as _json

        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        contract = _contract_fields(payload)
        scope_paths = _scope_paths_from_payload(payload)
        task_brief = {
            "task_id": task_id,
            "title": str(payload.get("title") or ""),
            "goal": str(payload.get("goal") or payload.get("description") or ""),
            "target_files": _target_files_from_payload(payload, scope_paths=scope_paths),
            "acceptance_criteria": list(contract["acceptance_criteria"]),
        }
        message = (
            "按「弱执行者蓝图纪律」把下面这个任务裂变为 construction_steps。\n"
            "你是拆分者,不是执行者: 不要读取文件,不要调用任何工具,不要输出 tool_call; "
            "只把后续 Director 应执行的步骤写成 JSON。\n"
            '只输出 JSON: {"construction_steps": [{"step_id", "target_file"(单文件), '
            '"est_lines"(整数,≤120), "signatures"(函数/类签名清单), '
            '"interface_names"(跨文件接口统一定名), '
            '"public_symbols"(本 target_file 必须定义/导出的精确符号), '
            '"consumes_symbols"({"另一个target_file":["本步实际导入/调用的精确符号"]}), '
            '"verify"(机器可执行判据), "depends_on"(step_id 列表), "title"}]}。\n'
            "verify 必须只包含可直接在 POSIX shell 执行的命令本身；"
            "禁止加入“通过/验证/说明/should/pass”等自然语言尾巴。\n"
            "跨文件任务必须先让提供方 target_file 在 public_symbols 中固定真实符号名，"
            "再让消费方用 consumes_symbols 引用完全相同的名字；禁止同一概念在不同文件使用不同名字，"
            "禁止让消费方凭空导入提供方没有声明的符号。\n"
            # 组合律 + 经济律 (live I3-r15): a strict linear chain (S2←S3←S4)
            # makes the whole parent only as strong as its weakest step — one
            # weak-executor failure cascade-kills every later step. depends_on
            # must be MINIMAL: list a step ONLY when this step's code literally
            # references another step's interface_names (e.g. main.js uses
            # index.html's element ids → depends_on that step). Independent files
            # (a stylesheet, a standalone README) keep depends_on EMPTY so they
            # fission as parallel, independently-recoverable work.
            "depends_on 必须最小化:仅当本步代码确实引用另一步声明的 interface_names 才填写;"
            "样式表/独立文档等不引用他文件符号的步骤,depends_on 留空,避免单步失败拖垮整个父任务。\n"
            f"任务契约:\n{_json.dumps(task_brief, ensure_ascii=False)}"
        )

        # 组合律: a sibling parent that already fissioned a shared file froze its
        # public identifiers in the ledger. Inject them so this parent reuses the
        # exact names instead of inventing colliding ones (live I3-r14: id=game
        # vs id=gameCanvas shipped a non-running product).
        declared = read_declared_interfaces(
            self._workspace,
            str(payload.get("cache_root", "")),
            list(task_brief["target_files"]),
        )
        message = message + render_assume_contract(declared)
        # 跨父文件归属 (I3-r18): a file already created by an earlier parent must be
        # EDITED, not rewritten — and this step must depends_on its owner (overrides
        # the depends_on-minimization default for that file). Only earlier parents'
        # ownership is visible here (this parent records after fission).
        owned_elsewhere = read_file_owners(
            self._workspace,
            str(payload.get("cache_root", "")),
            list(task_brief["target_files"]),
        )
        message = message + render_edit_contract(owned_elsewhere)

        def _invoke(extra: str = "") -> dict[str, Any]:
            has_loop = True
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                has_loop = False
            context = {
                "_transaction_kernel_forced_tool_definitions": [],
                "_transaction_kernel_forced_tool_choice": "none",
                "disable_internal_tool_rounds": True,
                "llm_call_timeout_seconds": 300,
                "request_timeout_seconds": 300,
                "timeout_seconds": 300,
                "cognitive_runtime_approval_mode": "auto_accept",
                "cognitive_runtime_approval_scope": "chief_engineer_step_fission_preflight",
                # Reasoning-sized output budget so the fission JSON survives
                # the model's thinking burn (I3-r17); clamped to the model's
                # max_output_tokens by the engine.
                "llm_max_tokens": _ce_fission_max_output_tokens(),
            }
            command = ExecuteRoleSessionCommandV1(
                role="chief_engineer",
                session_id=f"chief_engineer-fission-{task_id}",
                workspace=self._workspace,
                user_message=message + extra,
                run_id=str(payload.get("run_id") or "") or None,
                task_id=task_id,
                context=context,
                metadata={
                    "source": "chief_engineer.blueprint.ce_consumer",
                    "role_runtime_required": True,
                    "cognitive_runtime_required": True,
                    "cognitive_runtime_approval_mode": "auto_accept",
                    "cognitive_runtime_approval": {
                        "mode": "auto_accept",
                        "source": "factory_bench_headless_ce_fission",
                        "scope": "chief_engineer_step_fission_preflight",
                        "approved_by": "ce_consumer",
                    },
                    "context_os_expected": True,
                    "validate_output": False,
                },
                stream=False,
                host_kind="chief_engineer_blueprint",
                timeout_seconds=300,
            )
            runtime = RoleRuntimeService()
            coro = runtime.execute_role_session(command)
            if not has_loop:
                result = asyncio.run(coro)
                output = str(getattr(result, "output", "") or "")
                return {
                    "success": bool(getattr(result, "ok", False)),
                    "response": output,
                    "content": output,
                    "thinking": getattr(result, "thinking", None),
                    "role": str(getattr(result, "role", "chief_engineer") or "chief_engineer"),
                    "metadata": dict(getattr(result, "metadata", {}) or {}),
                    "execution_stats": dict(getattr(result, "usage", {}) or {}),
                    "tool_calls": list(getattr(result, "tool_calls", ()) or ()),
                    "artifacts": list(getattr(result, "artifacts", ()) or ()),
                    "error": str(getattr(result, "error_message", "") or getattr(result, "error_code", "") or ""),
                    "raw_response": result,
                }
            raise RuntimeError("ce_step_fission_inside_event_loop_unsupported")

        last_raw_head = {"text": ""}

        def _extract_steps(result: dict[str, Any]) -> list[dict[str, Any]]:
            import re as _re

            text = str(result.get("content") or result.get("response") or "")
            # Reasoning models (MiniMax-M3) can exhaust the output budget on
            # thinking and put the construction_steps JSON in the reasoning
            # channel with EMPTY content (I3-r17). role_dialogue surfaces that as
            # 'thinking'; scan it too so a completed-in-thinking answer is
            # recovered. validate_construction_steps below stays the sole
            # accept/reject authority, so this never relaxes fail-closed.
            thinking_blob = str(result.get("thinking") or "")
            last_raw_head["text"] = " ".join((text or thinking_blob).split())[:200]
            # Reasoning wrappers and fences drift run-to-run (live I3-r6: the
            # same model that emitted clean JSON in r5 returned think-wrapped
            # fenced output). Strip thinking, prefer fenced JSON, then scan
            # every balanced object via raw_decode until one carries steps.
            text = _re.sub(r"<think>.*?</think>", " ", text, flags=_re.DOTALL | _re.IGNORECASE)
            thinking_blob = _re.sub(r"<think>.*?</think>", " ", thinking_blob, flags=_re.DOTALL | _re.IGNORECASE)
            fenced = _re.findall(r"```(?:json)?\s*(.*?)```", text + "\n" + thinking_blob, flags=_re.DOTALL)
            decoder = _json.JSONDecoder()
            for blob in [*fenced, text, thinking_blob]:
                position = blob.find("{")
                while position != -1:
                    try:
                        data, _end = decoder.raw_decode(blob[position:])
                    except _json.JSONDecodeError:
                        position = blob.find("{", position + 1)
                        continue
                    raw_steps = data.get("construction_steps") if isinstance(data, dict) else None
                    if isinstance(raw_steps, list) and raw_steps:
                        return [
                            normalize_construction_step(item, parent_pm_task=task_id, index=i)
                            for i, item in enumerate(raw_steps)
                        ]
                    position = blob.find("{", position + 1)
            return []

        steps = _extract_steps(_invoke())
        errors = validate_construction_steps(steps, parent_pm_task=task_id)
        if errors and not steps:
            head = last_raw_head["text"] or "(empty model output)"
            errors = [f"{errors[0]} (raw head: {head})"]
        if errors:
            # One corrective re-ask with the gate errors quoted (same teaching
            # contract the Director-side ladders use).
            retry_extra = "\n上次输出未过质量门,逐条修正后重新输出完整 JSON:\n- " + "\n- ".join(errors[:8])
            steps = _extract_steps(_invoke(retry_extra))
            errors = validate_construction_steps(steps, parent_pm_task=task_id)
        return steps, errors

    def _publish_step_tasks(
        self,
        task_id: str,
        payload: dict[str, Any],
        steps: list[dict[str, Any]],
        *,
        blueprint_id: str,
        blueprint_path: str,
        prior_file_owners: dict[str, dict[str, str]] | None = None,
    ) -> int:
        """E1 fan-out: publish each construction step as a leaf pending_exec item.

        Cross-parent file ownership (I3-r18): when this step's target_file is
        already owned by an EARLIER parent's step, the owner is appended to
        ``depends_on`` so the market serializes this writer AFTER the owner
        (``_exec_claim_ready`` enforces it for free), and ``edit_on_prior`` is
        stamped so the Director extends the owner's content instead of clobbering
        it. The load-bearing guarantee is the serialization — even if the weak
        model ignores the prompt's edit instruction, the two writers can never run
        concurrently and last-write-wins is structurally impossible.
        """
        owners = prior_file_owners or {}
        published = 0
        run_id = str(payload.get("run_id", ""))
        for step in steps:
            step_id = str(step.get("step_id") or "")
            depends_on = list(step.get("depends_on") or ())
            owner = owners.get(_normalize_owned_target(step.get("target_file")))
            if owner and owner.get("owner_parent") != task_id and owner.get("owner_step_id") not in ("", step_id):
                owner_sid = owner["owner_step_id"]
                if owner_sid not in depends_on:
                    depends_on.append(owner_sid)
                step = {**step, "edit_on_prior": True, "edit_on_prior_owner": owner_sid}
            step_payload = {
                **{k: payload.get(k) for k in ("workspace", "run_id", "run_dir", "cache_root") if payload.get(k)},
                "title": step.get("title") or f"{payload.get('title', task_id)} · {step['step_id']}",
                "target_files": [step["target_file"]],
                "scope_paths": [step["target_file"]],
                "construction_step": step,
                "blueprint_id": blueprint_id,
                "blueprint_path": blueprint_path,
                "blueprint_hash": str(payload.get("blueprint_hash") or ""),
                "contract_hash": str(payload.get("contract_hash") or payload.get("pm_contract_hash") or ""),
                "pm_contract_hash": str(payload.get("contract_hash") or payload.get("pm_contract_hash") or ""),
                "job_token_id": str(payload.get("job_token_id") or ""),
                "job_token": _mapping(payload.get("job_token")),
                "control_plane_job_token": _mapping(payload.get("control_plane_job_token")),
                "capability_token": _mapping(payload.get("capability_token")),
                "control_plane_lineage": _mapping(payload.get("control_plane_lineage")),
                "route": "chief_blueprint_required",
                "acceptance_criteria": [step["verify"]] if step.get("verify") else [],
                "architecture_decisions": list(payload.get("architecture_decisions") or []),
                "selected_libraries": list(payload.get("selected_libraries") or []),
            }
            self._svc.publish_work_item(
                PublishTaskWorkItemCommandV1(
                    workspace=self._workspace,
                    trace_id=f"fission-{task_id}",
                    run_id=run_id,
                    task_id=str(step["step_id"]),
                    stage="pending_exec",
                    source_role="chief_engineer",
                    payload=step_payload,
                    parent_task_id=task_id,
                    root_task_id=str(payload.get("root_task_id") or task_id),
                    is_leaf=True,
                    depends_on=tuple(depends_on),
                    metadata={
                        "blueprint_id": blueprint_id,
                        "blueprint_hash": str(payload.get("blueprint_hash") or ""),
                        "job_token_id": str(payload.get("job_token_id") or ""),
                        "job_token": _mapping(payload.get("job_token")),
                        "control_plane_lineage": _mapping(payload.get("control_plane_lineage")),
                        "architecture_decisions": list(payload.get("architecture_decisions") or []),
                        "selected_libraries": list(payload.get("selected_libraries") or []),
                        "fission": "ce-blueprint-tasks/1",
                    },
                )
            )
            published += 1
        return published

    def _run_ce_preflight(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run CE preflight and return blueprint result dict.

        Args:
            task_id: Identifier of the task being processed.
            payload: Task payload dict from the task market.

        Returns:
            Blueprint result dict with ``blueprint_id``, ``guardrails``,
            ``no_touch_zones``, and ``scope_paths``.
        """
        # Resolve paths from payload, falling back to environment / workspace.
        resolved_workspace = str(payload.get("workspace", os.environ.get("KERNELONE_WORKSPACE", ""))).strip()
        run_dir = str(payload.get("run_dir", "")).strip()
        cache_root = str(payload.get("cache_root", "")).strip()
        run_id = str(payload.get("run_id", "")).strip()

        # Build task list from payload for PreflightContext.
        task_entry: dict[str, Any] = {
            "title": payload.get("title", task_id),
            **payload,
            "id": task_id,
        }

        # Build minimal run/events/dialogue paths.
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        metadata_dir = get_workspace_metadata_dir_name()
        events_path = os.path.join(resolved_workspace, metadata_dir, "runs", run_id, "events.json")
        dialogue_path = os.path.join(resolved_workspace, metadata_dir, "runs", run_id, "dialogue.jsonl")

        ctx = PreflightContext(
            workspace_full=resolved_workspace,
            cache_root_full=cache_root,
            run_dir=run_dir,
            run_id=run_id,
            pm_iteration=0,
            tasks=[task_entry],
            run_events=events_path,
            dialogue_full=dialogue_path,
            args=None,
            analysis_runner=self._analysis_runner,
            event_emitter=None,
        )

        result = run_pre_dispatch_chief_engineer_ctx(ctx)
        return {
            "blueprint_id": f"bp-{task_id}",
            "guardrails": result.get("blueprint_guardrails", []) if isinstance(result, dict) else [],
            "no_touch_zones": result.get("no_touch_zones", []) if isinstance(result, dict) else [],
            "scope_paths": payload.get("scope_paths", []),
            "doc_id": payload.get("doc_id", run_id or task_id),
        }

    def run(self) -> None:
        """Continuously process PENDING_DESIGN tasks until stop() is called."""
        logger.info(
            "CE consumer started: worker_id=%s workspace=%s idle_mode=event_wakeup",
            self._worker_id,
            self._workspace,
        )
        while not self._stop_event.is_set():
            try:
                self._work_event.clear()
                processed = self.poll_once()
                if not processed:
                    self._work_event.wait()
            except Exception as exc:
                logger.exception(
                    "CE consumer cycle failed, waiting for next wake signal: %s",
                    exc,
                )
                self._work_event.wait()
        logger.info("CE consumer stopped: worker_id=%s", self._worker_id)

    def stop(self) -> None:
        """Signal the consumer to stop after the current poll cycle."""
        self._stop_event.set()
        self._work_event.set()


__all__ = ["CEConsumer"]
