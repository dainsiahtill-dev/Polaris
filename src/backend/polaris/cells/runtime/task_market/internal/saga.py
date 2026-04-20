"""Saga compensation helpers for ``runtime.task_market``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import now_iso


@dataclass(frozen=True)
class CompensationAction:
    action_type: str
    target: str
    reverse_payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> CompensationAction:
        action_type = str(payload.get("action_type") or payload.get("type") or "").strip().lower()
        if not action_type:
            raise ValueError("compensation action_type is required")
        target = str(payload.get("target") or "").strip()
        if not target:
            raise ValueError("compensation target is required")
        reverse_payload_raw = payload.get("reverse_payload")
        reverse_payload = dict(reverse_payload_raw) if isinstance(reverse_payload_raw, dict) else {}
        return cls(
            action_type=action_type,
            target=target,
            reverse_payload=reverse_payload,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "target": self.target,
            "reverse_payload": dict(self.reverse_payload),
        }


class SagaCompensator:
    """Task-level compensation state machine stored in task metadata."""

    def register_action(self, item_metadata: dict[str, Any], action: CompensationAction) -> dict[str, Any]:
        state = self._get_state(item_metadata)
        if bool(state.get("committed", False)):
            state["committed"] = False
            state.pop("committed_at", None)
        state.pop("last_summary", None)
        state.pop("last_results", None)
        state.pop("compensated", None)
        state.pop("compensated_at", None)
        state.pop("requires_manual_intervention", None)
        actions = list(state.get("actions", []))
        actions.append(action.to_dict())
        state["actions"] = actions
        state["registered_at"] = now_iso()
        state["committed"] = bool(state.get("committed", False))
        item_metadata["saga_compensation"] = state
        return state

    def commit(self, item_metadata: dict[str, Any]) -> dict[str, Any]:
        state = self._get_state(item_metadata)
        action_count = len(state.get("actions", []))
        state["committed"] = True
        state["committed_at"] = now_iso()
        state["committed_action_count"] = action_count
        state["actions"] = []
        item_metadata["saga_compensation"] = state
        return state

    def compensate(
        self,
        *,
        item_metadata: dict[str, Any],
        workspace: str,
        reason: str,
        initiator: str,
    ) -> dict[str, Any]:
        state = self._get_state(item_metadata)
        if bool(state.get("committed", False)):
            summary = {
                "executed": False,
                "changed": False,
                "reason": "already_committed",
                "results": (),
                "requires_manual_intervention": False,
            }
            state["last_summary"] = dict(summary)
            item_metadata["saga_compensation"] = state
            return summary

        actions_raw = list(state.get("actions", []))
        actions: list[CompensationAction] = []
        for row in actions_raw:
            if not isinstance(row, dict):
                continue
            try:
                actions.append(CompensationAction.from_mapping(row))
            except ValueError:
                continue

        if not actions:
            summary = {
                "executed": False,
                "changed": False,
                "reason": "no_actions",
                "results": (),
                "requires_manual_intervention": False,
            }
            state["last_summary"] = dict(summary)
            item_metadata["saga_compensation"] = state
            return summary

        results: list[dict[str, Any]] = []
        requires_manual_intervention = False
        for action in reversed(actions):
            result = self._execute_action(workspace=workspace, action=action)
            results.append(result)
            if not bool(result.get("ok", False)):
                requires_manual_intervention = True

        completed_at = now_iso()
        state["compensated"] = True
        state["compensated_at"] = completed_at
        state["last_reason"] = str(reason or "").strip()
        state["last_initiator"] = str(initiator or "").strip()
        state["last_results"] = list(results)
        state["requires_manual_intervention"] = requires_manual_intervention
        state["actions"] = []
        item_metadata["saga_compensation"] = state

        return {
            "executed": True,
            "changed": True,
            "reason": "compensated",
            "results": tuple(results),
            "requires_manual_intervention": requires_manual_intervention,
        }

    def _execute_action(self, *, workspace: str, action: CompensationAction) -> dict[str, Any]:
        workspace_root = Path(workspace).resolve()
        try:
            target = self._resolve_safe_target(workspace_root, action.target)
            if action.action_type == "file_delete":
                if target.exists() and target.is_file():
                    target.unlink()
                return {"ok": True, "action_type": action.action_type, "target": str(target), "error": ""}
            if action.action_type == "file_restore_text":
                content = str(action.reverse_payload.get("content") or "")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                return {"ok": True, "action_type": action.action_type, "target": str(target), "error": ""}
            if action.action_type == "noop":
                return {"ok": True, "action_type": action.action_type, "target": str(target), "error": ""}
            return {
                "ok": False,
                "action_type": action.action_type,
                "target": str(target),
                "error": f"unsupported_action_type:{action.action_type}",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "action_type": action.action_type,
                "target": action.target,
                "error": str(exc),
            }

    def _resolve_safe_target(self, workspace_root: Path, target: str) -> Path:
        raw = Path(target)
        resolved = raw.resolve() if raw.is_absolute() else (workspace_root / raw).resolve()
        try:
            resolved.relative_to(workspace_root)
        except ValueError as exc:
            raise ValueError(f"target outside workspace: {target}") from exc
        return resolved

    def _get_state(self, item_metadata: dict[str, Any]) -> dict[str, Any]:
        raw_state = item_metadata.get("saga_compensation")
        state = dict(raw_state) if isinstance(raw_state, dict) else {}
        actions = state.get("actions")
        if not isinstance(actions, list):
            state["actions"] = []
        return state


__all__ = [
    "CompensationAction",
    "SagaCompensator",
]
