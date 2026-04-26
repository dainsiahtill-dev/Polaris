"""Governed bridge from approved Resident goals into PM runtime artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.audit.verdict.public.service import ArtifactService
from polaris.cells.runtime.projection.public.service import write_text_atomic
from polaris.domain.models.resident import GoalProposal, utc_now_iso
from polaris.kernelone.storage.io_paths import build_cache_root

if TYPE_CHECKING:
    from polaris.cells.resident.autonomy.internal.resident_storage import ResidentStorage


def _json_text(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n"


class ResidentPMBridge:
    """Stage approved Resident goals into governed PM-facing artifacts."""

    def __init__(self, storage: ResidentStorage, workspace: str, *, ramdisk_root: str = "") -> None:
        self._storage = storage  # type: ignore[misc]
        self.workspace = str(Path(workspace or ".").expanduser().resolve())
        self.cache_root = build_cache_root(str(ramdisk_root or ""), self.workspace)
        self.artifacts = ArtifactService(workspace=self.workspace, cache_root=self.cache_root)  # type: ignore[misc]
        self._backup_root = Path(self._storage.paths.root_dir) / "staging_backups"
        self._backup_root.mkdir(parents=True, exist_ok=True)

    def stage_goal(
        self,
        goal: GoalProposal,
        contract: Mapping[str, Any],
        *,
        promote_to_pm_runtime: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(contract, Mapping):
            raise ValueError("resident goal contract must be a mapping")

        now = utc_now_iso()
        staged_contract = self._build_staged_contract(goal, contract, staged_at=now)
        staged_plan = self._render_plan_block(goal, staged_contract, staged_at=now)

        resident_contract_path = self.artifacts.write_json("RESIDENT_GOAL_CONTRACT", staged_contract)
        resident_plan_path = self.artifacts.write_text("RESIDENT_GOAL_PLAN", staged_plan)

        result: dict[str, Any] = {
            "goal_id": goal.goal_id,
            "goal_status": goal.status.value,
            "staged_at": now,
            "promoted_to_pm_runtime": promote_to_pm_runtime,
            "contract": staged_contract,
            "artifacts": {
                "resident_contract_path": resident_contract_path,
                "resident_plan_path": resident_plan_path,
            },
            "pm_run": {
                "directive": self._build_run_directive(goal),
                "metadata": {
                    "resident_goal_id": goal.goal_id,
                    "resident_goal_type": goal.goal_type.value,
                    "resident_source": goal.source,
                    "resident_contract_path": resident_contract_path,
                    "resident_plan_path": resident_plan_path,
                    "promoted_to_pm_runtime": promote_to_pm_runtime,
                },
            },
        }

        if promote_to_pm_runtime:
            promotion = self._promote_to_pm_runtime(goal, staged_contract, staged_plan, staged_at=now)
            result["artifacts"].update(promotion["artifacts"])
            result["promotion"] = promotion

        return result

    def _promote_to_pm_runtime(
        self,
        goal: GoalProposal,
        staged_contract: Mapping[str, Any],
        staged_plan: str,
        *,
        staged_at: str,
    ) -> dict[str, Any]:
        backup_manifest = self._backup_active_pm_artifacts(goal.goal_id, staged_at=staged_at)
        existing_plan = self.artifacts.read_plan()
        merged_plan = self._upsert_plan_block(existing_plan, staged_plan, goal.goal_id)

        pm_contract_path = self.artifacts.write_task_contract(dict(staged_contract))
        pm_plan_path = self.artifacts.write_plan(merged_plan)

        pm_state = dict(self.artifacts.read_pm_state() or {})
        pm_state.update(
            {
                "resident_goal_id": goal.goal_id,
                "resident_goal_title": goal.title,
                "resident_goal_type": goal.goal_type.value,
                "resident_goal_source": goal.source,
                "resident_goal_scope": list(goal.scope),
                "resident_goal_budget": dict(goal.budget),
                "resident_goal_evidence_refs": list(goal.evidence_refs),
                "resident_goal_status": goal.status.value,
                "resident_goal_staged_at": staged_at,
                "resident_goal_contract_path": pm_contract_path,
                "resident_goal_plan_path": pm_plan_path,
            }
        )
        pm_state_path = self.artifacts.write_pm_state(pm_state)

        return {
            "promoted_at": staged_at,
            "artifacts": {
                "pm_contract_path": pm_contract_path,
                "pm_plan_path": pm_plan_path,
                "pm_state_path": pm_state_path,
                "backup_manifest_path": backup_manifest["manifest_path"],
                "backup_dir": backup_manifest["backup_dir"],
            },
            "backup": backup_manifest,
        }

    def _build_staged_contract(
        self,
        goal: GoalProposal,
        contract: Mapping[str, Any],
        *,
        staged_at: str,
    ) -> dict[str, Any]:
        metadata = (
            dict(contract.get("metadata"))  # type: ignore[arg-type]
            if isinstance(contract.get("metadata"), Mapping)
            else {}
        )
        metadata.update(
            {
                "resident_goal_id": goal.goal_id,
                "resident_goal_type": goal.goal_type.value,
                "resident_source": goal.source,
                "resident_scope": list(goal.scope),
                "resident_budget": dict(goal.budget),
                "resident_evidence_refs": list(goal.evidence_refs),
                "resident_approval_note": goal.approval_note,
                "resident_staged_at": staged_at,
            }
        )

        raw_tasks = contract.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise ValueError("resident goal contract must contain tasks")

        tasks: list[dict[str, Any]] = []
        for raw_task in raw_tasks:
            if not isinstance(raw_task, Mapping):
                continue
            task_payload = dict(raw_task)
            task_metadata = (
                dict(task_payload.get("metadata"))  # type: ignore[arg-type]
                if isinstance(task_payload.get("metadata"), Mapping)
                else {}
            )
            task_metadata.update(
                {
                    "resident_goal_id": goal.goal_id,
                    "resident_goal_type": goal.goal_type.value,
                    "resident_source": goal.source,
                }
            )
            task_payload["metadata"] = task_metadata
            tasks.append(task_payload)
        if not tasks:
            raise ValueError("resident goal contract tasks are empty after validation")

        return {
            "focus": str(contract.get("focus") or "resident_goal_materialization").strip()
            or "resident_goal_materialization",
            "overall_goal": str(contract.get("overall_goal") or goal.title).strip() or goal.title,
            "metadata": metadata,
            "tasks": tasks,
        }

    def _render_plan_block(
        self,
        goal: GoalProposal,
        contract: Mapping[str, Any],
        *,
        staged_at: str,
    ) -> str:
        lines: list[str] = [
            self._begin_marker(goal.goal_id),
            "# Resident Goal Staging",
            "",
            f"- Goal ID: `{goal.goal_id}`",
            f"- Title: {goal.title or 'Resident goal'}",
            f"- Type: `{goal.goal_type.value}`",
            f"- Source: `{goal.source or 'resident'}`",
            f"- Status: `{goal.status.value}`",
            f"- Staged At: `{staged_at}`",
            "",
            "## Motivation",
            "",
            goal.motivation or "No motivation recorded.",
            "",
            "## Scope",
            "",
        ]
        scope_items = list(goal.scope) or ["src/backend", "docs"]
        lines.extend([f"- `{item}`" for item in scope_items])
        lines.extend(["", "## Evidence Refs", ""])
        if goal.evidence_refs:
            lines.extend([f"- `{item}`" for item in goal.evidence_refs])
        else:
            lines.append("- None supplied")
        lines.extend(["", "## Budget", ""])
        if goal.budget:
            for key, value in dict(goal.budget).items():
                lines.append(f"- `{key}`: `{value}`")
        else:
            lines.append("- Default governed budget")
        lines.extend(["", "## PM Task Outline", ""])

        raw_tasks = contract.get("tasks")
        tasks = raw_tasks if isinstance(raw_tasks, list) else []
        for index, raw_task in enumerate(tasks, start=1):
            if not isinstance(raw_task, Mapping):
                continue
            lines.append(f"### Task {index}: {str(raw_task.get('title') or 'Untitled task').strip()}")
            lines.append("")
            lines.append(f"- Role: `{str(raw_task.get('assigned_to') or 'unassigned').strip()}`")
            lines.append(f"- Phase: `{str(raw_task.get('phase') or 'implementation').strip()}`")
            goal_text = str(raw_task.get("goal") or "").strip()
            if goal_text:
                lines.append(f"- Goal: {goal_text}")
            checklist = raw_task.get("execution_checklist")
            if isinstance(checklist, list) and checklist:
                lines.append("- Checklist:")
                lines.extend([f"  - {str(item).strip()}" for item in checklist if str(item).strip()])
            acceptance = raw_task.get("acceptance_criteria")
            if isinstance(acceptance, list) and acceptance:
                lines.append("- Acceptance:")
                lines.extend([f"  - {str(item).strip()}" for item in acceptance if str(item).strip()])
            lines.append("")

        lines.append(self._end_marker(goal.goal_id))
        return "\n".join(lines).strip() + "\n"

    def _build_run_directive(self, goal: GoalProposal) -> str:
        title = goal.title.replace("`", "").strip() or "Resident goal"
        return (
            f"Refine and execute the approved Resident goal '{title}'. "
            "Use runtime/contracts/resident.goal.contract.json as the governed task skeleton, "
            "use runtime/contracts/resident.goal.plan.md as the planning addendum, "
            "preserve resident_goal_id metadata in any rewritten PM contract, "
            "and keep the execution bounded to the approved Resident scope and evidence refs."
        )

    def _backup_active_pm_artifacts(self, goal_id: str, *, staged_at: str) -> dict[str, Any]:
        backup_dir = self._backup_root / goal_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, Any] = {
            "goal_id": goal_id,
            "staged_at": staged_at,
            "backup_dir": str(backup_dir),
            "artifacts": {},
        }

        artifact_specs = (
            ("contract.pm_tasks", "pm_tasks.contract.json", "json"),
            ("contract.plan", "plan.md", "text"),
            ("runtime.state.pm", "pm.state.json", "json"),
        )
        for artifact_key, backup_name, artifact_kind in artifact_specs:
            if not self.artifacts.exists(artifact_key):
                continue
            backup_path = backup_dir / backup_name
            source_path = self.artifacts.get_path(artifact_key)
            if artifact_kind == "json":
                payload = self.artifacts.read_json(artifact_key)
                if not isinstance(payload, Mapping):
                    continue
                write_text_atomic(str(backup_path), _json_text(payload))
            else:
                write_text_atomic(str(backup_path), self.artifacts.read_text(artifact_key))
            manifest["artifacts"][artifact_key] = {
                "source_path": source_path,
                "backup_path": str(backup_path),
            }

        manifest_path = backup_dir / "manifest.json"
        write_text_atomic(str(manifest_path), _json_text(manifest))
        manifest["manifest_path"] = str(manifest_path)
        return manifest

    def _upsert_plan_block(self, existing_plan: str, block: str, goal_id: str) -> str:
        begin = self._begin_marker(goal_id)
        end = self._end_marker(goal_id)
        existing = str(existing_plan or "")
        start_index = existing.find(begin)
        end_index = existing.find(end)
        if start_index >= 0 and end_index > start_index:
            replacement_end = end_index + len(end)
            updated = existing[:start_index].rstrip() + "\n\n" + block.strip() + "\n"
            tail = existing[replacement_end:].lstrip("\n")
            if tail:
                updated += "\n" + tail
            return updated.strip() + "\n"
        if existing.strip():
            return existing.rstrip() + "\n\n" + block.strip() + "\n"
        return block.strip() + "\n"

    @staticmethod
    def _begin_marker(goal_id: str) -> str:
        return f"<!-- RESIDENT_GOAL:{goal_id}:BEGIN -->"

    @staticmethod
    def _end_marker(goal_id: str) -> str:
        return f"<!-- RESIDENT_GOAL:{goal_id}:END -->"


__all__ = ["ResidentPMBridge"]
