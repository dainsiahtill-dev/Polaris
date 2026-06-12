"""CE consumer that polls PENDING_DESIGN and generates blueprints."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from polaris.cells.chief_engineer.blueprint.internal.adr_store import ADRStore
from polaris.cells.chief_engineer.blueprint.internal.blueprint_persistence import (
    BlueprintPersistence,
)
from polaris.cells.chief_engineer.blueprint.internal.chief_engineer_preflight import (
    PreflightContext,
    run_pre_dispatch_chief_engineer_ctx,
)
from polaris.cells.chief_engineer.blueprint.internal.step_contract import (
    build_blueprint_tasks_contract,
    normalize_construction_step,
    validate_construction_steps,
)
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
    PublishTaskWorkItemCommandV1,
)
from polaris.cells.runtime.task_market.public.service import get_task_market_service

logger = logging.getLogger(__name__)


def _blueprint_runtime_path(blueprint_id: str) -> str:
    return f"runtime/blueprints/{blueprint_id}.json"


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


def _payload_task(payload: dict[str, Any]) -> dict[str, Any]:
    return _mapping(payload.get("pm_contract")) or _mapping(payload.get("task"))


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
    return {
        "task": task,
        "qa_contract": qa_contract,
        "acceptance_criteria": acceptance_criteria,
        "execution_checklist": execution_checklist,
        "dependencies": dependencies,
        "constraints": constraints,
        "risks": risks,
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
        poll_interval: Seconds to sleep between poll cycles when no task is found.
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
        self._poll_interval = float(poll_interval)
        self._stop_event = threading.Event()
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
            scope_paths = _string_list(blueprint_result.get("scope_paths")) or _string_list(payload.get("scope_paths"))
            target_files = (
                _string_list(blueprint_result.get("target_files"))
                or _string_list(payload.get("target_files"))
                or list(scope_paths)
            )
            contract = _contract_fields(payload)
            acceptance_criteria = list(contract["acceptance_criteria"])
            execution_checklist = list(contract["execution_checklist"])
            dependencies = list(contract["dependencies"])
            constraints = dict(contract["constraints"])
            risks = list(contract["risks"])
            contract_completeness = _contract_completeness(
                target_files=target_files,
                acceptance_criteria=acceptance_criteria,
                execution_checklist=execution_checklist,
            )
            blueprint_path = _blueprint_runtime_path(blueprint_id)
            ack_payload: dict[str, Any] = {
                "blueprint_id": blueprint_id,
                "blueprint_path": blueprint_path,
                "runtime_blueprint_path": blueprint_path,
                "context_snapshot_ref": str(payload.get("context_snapshot_ref", "")),
                "guardrails": blueprint_result.get("guardrails", []),
                "no_touch_zones": blueprint_result.get("no_touch_zones", []),
                "scope_paths": scope_paths,
                "target_files": target_files,
                "acceptance_criteria": acceptance_criteria,
                "execution_checklist": execution_checklist,
                "dependencies": dependencies,
                "constraints": constraints,
                "risks": risks,
                "pm_contract": dict(contract["task"]),
                "qa_contract": dict(contract["qa_contract"]),
                "contract_completeness": contract_completeness,
                "handoff_ready": bool(contract_completeness["handoff_ready"]),
                "route": "chief_blueprint_required",
                "task_market_route": "chief_blueprint_required",
                "blueprint_required": True,
            }

            if self._enable_director_pool and self._adr_store is not None:
                self._adr_store.create_blueprint(
                    blueprint_id,
                    {
                        "task_id": task_id,
                        "run_id": str(payload.get("run_id", "")),
                        "route": "chief_blueprint_required",
                        "preflight_result": blueprint_result,
                        "scope_paths": scope_paths,
                        "target_files": target_files,
                        "acceptance_criteria": acceptance_criteria,
                        "execution_checklist": execution_checklist,
                        "dependencies": dependencies,
                        "constraints": constraints,
                        "risks": risks,
                        "pm_task": dict(contract["task"]),
                        "qa_contract": dict(contract["qa_contract"]),
                        "contract_completeness": contract_completeness,
                        "handoff_ready": bool(contract_completeness["handoff_ready"]),
                        "guardrails": ack_payload["guardrails"],
                        "no_touch_zones": ack_payload["no_touch_zones"],
                    },
                )
                self._adr_store.compile(blueprint_id)

                ack_payload["director_pool_assignment"] = "deferred_to_task_market"
            else:
                BlueprintPersistence(self._workspace).save(
                    blueprint_id,
                    {
                        "schema_version": "chief_engineer.blueprint.v1",
                        "blueprint_id": blueprint_id,
                        "status": "approved",
                        "task_id": task_id,
                        "run_id": str(payload.get("run_id", "")),
                        "route": "chief_blueprint_required",
                        "scope_paths": scope_paths,
                        "target_files": target_files,
                        "acceptance_criteria": acceptance_criteria,
                        "execution_checklist": execution_checklist,
                        "dependencies": dependencies,
                        "constraints": constraints,
                        "risks": risks,
                        "pm_task": dict(contract["task"]),
                        "qa_contract": dict(contract["qa_contract"]),
                        "contract_completeness": contract_completeness,
                        "handoff_ready": bool(contract_completeness["handoff_ready"]),
                        "guardrails": ack_payload["guardrails"],
                        "no_touch_zones": ack_payload["no_touch_zones"],
                        "preflight_result": blueprint_result,
                    },
                )

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
                    payload,
                    steps,
                    blueprint_id=blueprint_id,
                    blueprint_path=blueprint_path,
                )
                ack_payload["construction_steps"] = steps
                ack_payload["fission_step_count"] = fission_published
                # The parent becomes a non-leaf supervision row; Director
                # workers must claim only leaf steps (I2: leaf-only claim gate).
                ack_payload["is_leaf"] = False

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

        from polaris.bootstrap.config import get_settings
        from polaris.cells.llm.dialogue.internal.role_dialogue import generate_role_response

        contract = _contract_fields(payload)
        task_brief = {
            "task_id": task_id,
            "title": str(payload.get("title") or ""),
            "goal": str(payload.get("goal") or payload.get("description") or ""),
            "target_files": _string_list(payload.get("target_files")),
            "acceptance_criteria": list(contract["acceptance_criteria"]),
        }
        message = (
            "按「弱执行者蓝图纪律」把下面这个任务裂变为 construction_steps。\n"
            '只输出 JSON: {"construction_steps": [{"step_id", "target_file"(单文件), '
            '"est_lines"(整数,≤120), "signatures"(函数/类签名清单), '
            '"interface_names"(跨文件接口统一定名), "verify"(机器可执行判据), '
            '"depends_on"(step_id 列表), "title"}]}。\n'
            f"任务契约:\n{_json.dumps(task_brief, ensure_ascii=False)}"
        )

        def _invoke(extra: str = "") -> dict[str, Any]:
            has_loop = True
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                has_loop = False
            coro = generate_role_response(
                workspace=self._workspace,
                settings=get_settings(),
                role="chief_engineer",
                message=message + extra,
                # Pure text-generation contract (same as the PM planning path):
                # reasoning planners answer tool-bearing requests WITH
                # tool_calls and the visible text collector sees nothing.
                context={
                    "_transaction_kernel_forced_tool_definitions": [],
                    "_transaction_kernel_forced_tool_choice": "none",
                    "disable_internal_tool_rounds": True,
                    "llm_call_timeout_seconds": 300,
                    "request_timeout_seconds": 300,
                    "timeout_seconds": 300,
                },
                # The fission JSON is its own contract (step gate below);
                # the CE blueprint checklist would zero-score it.
                validate_output=False,
            )
            if not has_loop:
                return asyncio.run(coro)
            raise RuntimeError("ce_step_fission_inside_event_loop_unsupported")

        last_raw_head = {"text": ""}

        def _extract_steps(result: dict[str, Any]) -> list[dict[str, Any]]:
            import re as _re

            text = str(result.get("content") or result.get("response") or "")
            last_raw_head["text"] = " ".join(text.split())[:200]
            # Reasoning wrappers and fences drift run-to-run (live I3-r6: the
            # same model that emitted clean JSON in r5 returned think-wrapped
            # fenced output). Strip thinking, prefer fenced JSON, then scan
            # every balanced object via raw_decode until one carries steps.
            text = _re.sub(r"<think>.*?</think>", " ", text, flags=_re.DOTALL | _re.IGNORECASE)
            fenced = _re.findall(r"```(?:json)?\s*(.*?)```", text, flags=_re.DOTALL)
            decoder = _json.JSONDecoder()
            for blob in [*fenced, text]:
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
    ) -> int:
        """E1 fan-out: publish each construction step as a leaf pending_exec item."""
        published = 0
        run_id = str(payload.get("run_id", ""))
        for step in steps:
            step_payload = {
                **{k: payload.get(k) for k in ("workspace", "run_id", "run_dir", "cache_root") if payload.get(k)},
                "title": step.get("title") or f"{payload.get('title', task_id)} · {step['step_id']}",
                "target_files": [step["target_file"]],
                "scope_paths": [step["target_file"]],
                "construction_step": step,
                "blueprint_id": blueprint_id,
                "blueprint_path": blueprint_path,
                "route": "chief_blueprint_required",
                "acceptance_criteria": [step["verify"]] if step.get("verify") else [],
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
                    depends_on=tuple(step.get("depends_on") or ()),
                    metadata={"blueprint_id": blueprint_id, "fission": "ce-blueprint-tasks/1"},
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
        """Continuously poll and process PENDING_DESIGN tasks until stop() is called."""
        logger.info(
            "CE consumer started: worker_id=%s workspace=%s poll_interval=%.1f",
            self._worker_id,
            self._workspace,
            self._poll_interval,
        )
        while not self._stop_event.is_set():
            try:
                processed = self.poll_once()
                if not processed:
                    self._stop_event.wait(self._poll_interval)
            except Exception as exc:
                logger.exception(
                    "CE consumer poll cycle failed, retrying in %.1fs: %s",
                    self._poll_interval,
                    exc,
                )
                self._stop_event.wait(self._poll_interval)
        logger.info("CE consumer stopped: worker_id=%s", self._worker_id)

    def stop(self) -> None:
        """Signal the consumer to stop after the current poll cycle."""
        self._stop_event.set()


__all__ = ["CEConsumer"]
