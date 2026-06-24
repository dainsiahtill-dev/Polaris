"""Resident AGI role adapter.

Resident AGI is a platform-level supervisory role, not an execution shortcut.
This adapter exists only to enter the shared RoleRuntime / ContextOS /
TurnEngine path with the ``resident_agi`` role profile. Tool permissions and
write boundaries remain defined by ``core_roles.yaml``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .base import BaseRoleAdapter
from .runtime_dialogue import invoke_role_runtime_first


class ResidentAgiAdapter(BaseRoleAdapter):
    """Adapter for platform-level AGI supervision turns."""

    @property
    def role_id(self) -> str:
        return "resident_agi"

    def get_capabilities(self) -> list[str]:
        return [
            "platform_supervision",
            "contextos_final_request_audit",
            "decision_trace_review",
            "goal_governance",
            "quality_gate_triage",
        ]

    async def execute(
        self,
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a Resident AGI decision turn through the shared role runtime."""

        decision_type = self._string(input_data.get("decision_type")) or "platform_supervision"
        objective = self._resolve_objective(input_data)
        if not objective:
            return {
                "success": False,
                "stage": "resident_agi",
                "decision_type": decision_type,
                "error": "objective must be a non-empty string",
            }

        self._update_task_progress(task_id, "deciding")
        runtime_context = self._build_runtime_context(
            task_id=task_id,
            decision_type=decision_type,
            objective=objective,
            input_data=input_data,
            context=context,
        )
        message = self._build_decision_message(
            decision_type=decision_type,
            objective=objective,
            input_data=input_data,
        )

        try:
            response = await invoke_role_runtime_first(
                workspace=self.workspace,
                role=self.role_id,
                message=message,
                context=runtime_context,
                domain="resident_agi_decision",
                validate_output=False,
                max_retries=1,
            )
        except (RuntimeError, ValueError) as exc:
            self._update_task_progress(task_id, "blocked")
            return {
                "success": False,
                "stage": "resident_agi",
                "decision_type": decision_type,
                "error": str(exc),
            }

        content = str(response.get("response") or response.get("content") or "")
        error = self._string(response.get("error"))
        decision_payload = self._extract_json_object(content)
        success = bool(response.get("success")) and bool(content.strip()) and not bool(error)
        self._update_task_progress(task_id, "completed" if success else "blocked")
        return {
            "success": success,
            "stage": "resident_agi",
            "decision_type": decision_type,
            "objective": objective,
            "content": content,
            "decision": decision_payload,
            "metadata": dict(response.get("metadata") or {}),
            "tool_calls": list(response.get("tool_calls") or []),
            "execution_stats": dict(response.get("execution_stats") or {}),
            "error": error or None,
        }

    @staticmethod
    def _string(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _resolve_objective(cls, input_data: Mapping[str, Any]) -> str:
        for key in ("objective", "message", "prompt", "summary"):
            value = cls._string(input_data.get(key))
            if value:
                return value
        return ""

    @classmethod
    def _build_runtime_context(
        cls,
        *,
        task_id: str,
        decision_type: str,
        objective: str,
        input_data: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> dict[str, Any]:
        runtime_context = dict(context or {})
        metadata = dict(runtime_context.get("metadata") or {})
        metadata.update(
            {
                "resident_agi_role_runtime_required": True,
                "resident_agi_contextos_required": True,
                "resident_agi_turn_engine_required": True,
                "decision_type": decision_type,
            }
        )
        runtime_context.update(
            {
                "task_id": str(task_id or "").strip(),
                "decision_type": decision_type,
                "objective": objective,
                "evidence": cls._mapping(input_data.get("evidence")),
                "constraints": cls._sequence(input_data.get("constraints")),
                "candidate_actions": cls._sequence(input_data.get("candidate_actions")),
                "metadata": metadata,
            }
        )
        return runtime_context

    @classmethod
    def _build_decision_message(
        cls,
        *,
        decision_type: str,
        objective: str,
        input_data: Mapping[str, Any],
    ) -> str:
        payload = {
            "decision_type": decision_type,
            "objective": objective,
            "evidence": cls._mapping(input_data.get("evidence")),
            "constraints": cls._sequence(input_data.get("constraints")),
            "candidate_actions": cls._sequence(input_data.get("candidate_actions")),
            "required_output": {
                "verdict": "continue|block|escalate|request_evidence",
                "rationale": "evidence-backed reason",
                "evidence_refs": ["context/ref/path"],
                "risks": ["risk"],
                "next_action": "specific next action",
                "downstream_allowed": False,
            },
        }
        return "\n".join(
            [
                "Run a Resident AGI supervision decision through the shared role runtime.",
                "Do not bypass PM -> Chief Engineer -> Director -> QA.",
                "Return one JSON object matching required_output.",
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            ]
        )

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _sequence(value: Any) -> list[Any]:
        if value is None or isinstance(value, str | bytes):
            return []
        if isinstance(value, Sequence):
            return list(value)
        return []

    @staticmethod
    def _extract_json_object(content: str) -> dict[str, Any]:
        text = str(content or "").strip()
        if not text:
            return {}
        decoder = json.JSONDecoder()
        candidates = [text]
        if text.startswith("```"):
            candidates.extend(part.strip() for part in text.split("```") if part.strip())
        for candidate in candidates:
            cleaned = candidate
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
            for index, char in enumerate(cleaned):
                if char != "{":
                    continue
                try:
                    parsed, _ = decoder.raw_decode(cleaned[index:])
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
        return {}


__all__ = ["ResidentAgiAdapter"]
