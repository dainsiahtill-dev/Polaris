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
        decision_contract = cls._build_decision_contract(
            decision_type=decision_type,
            objective=objective,
            input_data=input_data,
        )
        metadata = dict(runtime_context.get("metadata") or {})
        metadata.update(
            {
                "resident_agi_role_runtime_required": True,
                "resident_agi_contextos_required": True,
                "resident_agi_turn_engine_required": True,
                "decision_type": decision_type,
                "resident_agi_decision_contract_schema": decision_contract["schema_version"],
                "resident_agi_selected_decision_capability": decision_contract["decision_capability_id"],
                "resident_agi_required_evidence_interfaces": decision_contract["required_evidence_interfaces"],
                "resident_agi_optional_evidence_interfaces": decision_contract["optional_evidence_interfaces"],
            }
        )
        runtime_context.update(
            {
                "task_id": str(task_id or "").strip(),
                "decision_type": decision_type,
                "objective": objective,
                "resident_agi_decision_contract": decision_contract,
                "selected_decision_capability": decision_contract["selected_decision_capability"],
                "required_evidence_interfaces": decision_contract["required_evidence_interfaces"],
                "optional_evidence_interfaces": decision_contract["optional_evidence_interfaces"],
                "evidence": cls._mapping(input_data.get("evidence")),
                "resident_agi_audit_pack": cls._mapping(input_data.get("resident_agi_audit_pack")),
                "constraints": decision_contract["constraints"],
                "candidate_actions": decision_contract["candidate_actions"],
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
        decision_contract = cls._build_decision_contract(
            decision_type=decision_type,
            objective=objective,
            input_data=input_data,
        )
        payload = {
            "resident_agi_decision_contract": decision_contract,
            "evidence": cls._mapping(input_data.get("evidence")),
            "required_output": {
                "verdict": "continue|block|escalate|request_evidence",
                "rationale": "evidence-backed reason",
                "evidence_refs": ["context/ref/path"],
                "risks": ["risk"],
                "next_action": "specific next action",
                "downstream_allowed": False,
                "decision_capability_id": decision_contract["decision_capability_id"],
            },
        }
        return "\n".join(
            [
                "Run a Resident AGI supervision decision through the shared role runtime.",
                "Use resident_agi_decision_contract as the governing decision contract.",
                "Do not infer a different AGI capability from unrelated audit-pack registry entries.",
                "Do not bypass PM → Chief Engineer → Director.",
                "Return one JSON object matching required_output.",
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            ]
        )

    @classmethod
    def _build_decision_contract(
        cls,
        *,
        decision_type: str,
        objective: str,
        input_data: Mapping[str, Any],
    ) -> dict[str, Any]:
        selected_capability = cls._selected_decision_capability_contract(input_data)
        decision_capability_id = cls._string(selected_capability.get("decision_id"))
        required_interfaces = cls._non_empty_strings(
            cls._sequence(input_data.get("required_evidence_interfaces"))
        ) or cls._non_empty_strings(cls._sequence(selected_capability.get("required_evidence_interfaces")))
        optional_interfaces = cls._non_empty_strings(
            cls._sequence(input_data.get("optional_evidence_interfaces"))
        ) or cls._non_empty_strings(cls._sequence(selected_capability.get("optional_evidence_interfaces")))
        candidate_actions = cls._non_empty_strings(cls._sequence(input_data.get("candidate_actions"))) or (
            cls._non_empty_strings(cls._sequence(selected_capability.get("candidate_actions")))
        )
        constraints = cls._non_empty_strings(cls._sequence(input_data.get("constraints"))) or (
            cls._non_empty_strings(cls._sequence(selected_capability.get("hard_constraints")))
        )
        return {
            "schema_version": "resident.agi_decision_contract.v1",
            "role_id": "resident_agi",
            "runtime_foundation": "roles.runtime + ContextOS + TurnEngine",
            "decision_type": decision_type,
            "objective": objective,
            "decision_capability_id": decision_capability_id,
            "selected_decision_capability": selected_capability,
            "required_evidence_interfaces": required_interfaces,
            "optional_evidence_interfaces": optional_interfaces,
            "candidate_actions": candidate_actions,
            "constraints": constraints,
            "context_refs": cls._non_empty_strings(cls._sequence(input_data.get("context_refs"))),
            "evidence_refs": cls._non_empty_strings(cls._sequence(input_data.get("evidence_refs"))),
            "audit_summary": cls._resident_agi_audit_summary(cls._mapping(input_data.get("resident_agi_audit_pack"))),
        }

    @classmethod
    def _selected_decision_capability_contract(
        cls,
        input_data: Mapping[str, Any],
    ) -> dict[str, Any]:
        selected = cls._mapping(input_data.get("selected_decision_capability"))
        result: dict[str, Any] = {}
        for key in (
            "decision_id",
            "name",
            "owner",
            "decision_scope",
            "risk_level",
            "escalation",
            "output_contract",
        ):
            value = cls._string(selected.get(key))
            if value:
                result[key] = value
        for key in (
            "required_evidence_interfaces",
            "optional_evidence_interfaces",
            "candidate_actions",
            "hard_constraints",
            "contract_refs",
        ):
            values = cls._non_empty_strings(cls._sequence(selected.get(key)))
            if values:
                result[key] = values
        if "llm_decision_required" in selected:
            result["llm_decision_required"] = bool(selected.get("llm_decision_required"))
        if "platform_enforced" in selected:
            result["platform_enforced"] = bool(selected.get("platform_enforced"))
        return result

    @classmethod
    def _resident_agi_audit_summary(cls, audit_pack: Mapping[str, Any]) -> dict[str, Any]:
        if not audit_pack:
            return {}
        capability_surface = cls._mapping(audit_pack.get("capability_surface"))
        decision_registry = cls._mapping(capability_surface.get("decision_capability_registry"))
        decision_profile = cls._mapping(audit_pack.get("decision_profile"))
        if not decision_registry:
            decision_registry = cls._mapping(decision_profile.get("decision_capability_registry"))
        role_registry = cls._mapping(audit_pack.get("role_registry"))
        return {
            "schema_version": cls._string(audit_pack.get("schema_version")),
            "role_id": cls._string(audit_pack.get("role_id")),
            "truth_sources": cls._non_empty_strings(cls._sequence(audit_pack.get("truth_sources"))),
            "role_registry": cls._pick_mapping(
                role_registry,
                ("resident_agi_available", "role_ids", "role_profile_count"),
            ),
            "hard_rule_gate": cls._pick_mapping(
                cls._mapping(audit_pack.get("hard_rule_gate")),
                ("schema_version", "status", "passed", "reason", "failed_check_ids"),
            ),
            "evidence_gate": cls._pick_mapping(
                cls._mapping(audit_pack.get("evidence_gate")),
                ("schema_version", "status", "recommended_verdict", "reason", "missing_evidence_refs"),
            ),
            "authority_matrix": cls._pick_mapping(
                cls._mapping(audit_pack.get("authority_matrix")),
                ("schema_version", "chain_required", "runtime_foundation"),
            ),
            "decision_profile": cls._pick_mapping(
                decision_profile,
                (
                    "schema_version",
                    "recommended_verdict",
                    "recommended_next_action",
                    "role_turn_allowed",
                    "downstream_precheck",
                ),
            ),
            "decision_capability_registry": cls._pick_mapping(
                decision_registry,
                ("schema_version", "role_id", "runtime_foundation", "counts", "decision_policy"),
            ),
        }

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

    @classmethod
    def _non_empty_strings(cls, values: Sequence[Any]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            token = cls._string(value)
            if not token or token in seen:
                continue
            seen.add(token)
            result.append(token)
        return result

    @classmethod
    def _pick_mapping(cls, value: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in keys:
            if key not in value:
                continue
            item = value.get(key)
            if isinstance(item, Mapping):
                compact_item = cls._mapping(item)
                if compact_item:
                    result[key] = compact_item
            elif isinstance(item, Sequence) and not isinstance(item, str | bytes):
                compact_sequence = list(item)
                if compact_sequence:
                    result[key] = compact_sequence
            elif item is not None and item != "":
                result[key] = item
        return result

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
