"""
HP Protocol - Public contracts.

Public interface for the Polaris Protocol 7-phase state machine,
exposed by the `policy.permission` cell.

Public exports:
    PolicyRunState  - Runtime state of an HP Protocol run
    PolicyRuntime   - Stateful executor for HP Protocol phases
    PolicyContractError - Error raised on contract violations
    HPProtocolService    - High-level service facade (public-facing)
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polaris.kernelone.utils.time_utils import utc_now_iso

# HP Protocol - 7 Phase Ritual (符节状态机)
# Each phase must complete in sequence before proceeding to the next
HP_PIPELINE: list[str] = [
    "start_run",  # 拟诏 - Define goals and acceptance criteria
    "blueprint",  # 制图 - Create implementation plan
    "policy_check",  # 廷议 - Policy and budget approval
    "snapshot",  # 快照 - Backup current state
    "implementation",  # 动工 - Execute code generation
    "verify",  # 勘验 - Self-check (compile/type check)
    "finalize",  # 归档 - Complete and record results
]


class PolicyContractError(RuntimeError):
    """Raised on HP Protocol contract violations (phase order, missing prerequisites)."""

    pass


# Backward compatibility alias
_utc_now_iso = utc_now_iso


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


@dataclass
class PolicyRunState:
    run_id: str
    phase_index: int = -1
    approved: bool = False
    contract_set: bool = False
    blueprint_set: bool = False
    snapshot_set: bool = False
    verification_set: bool = False
    seq: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyRuntime:
    """
    Stateful executor for HP Protocol phases.

    All phase methods (hp_*) validate preconditions and advance the phase
    machine atomically, writing both a JSONL event stream and a JSON state
    snapshot on every transition.
    """

    workspace: Path
    events_path: Path
    sentinel_path: Path
    state_path: Path
    actor: str = "Director"
    state: PolicyRunState = field(default_factory=lambda: PolicyRunState(run_id=f"run_{uuid.uuid4().hex[:8]}"))

    @classmethod
    def create(
        cls,
        workspace: str | Path,
        *,
        run_id: str | None = None,
        actor: str = "Director",
    ) -> PolicyRuntime:
        from polaris.kernelone.storage import (
            resolve_runtime_path,
            resolve_workspace_persistent_path,
        )

        ws = Path(workspace).resolve()
        state = PolicyRunState(run_id=run_id or f"run_{uuid.uuid4().hex[:8]}")
        events_path = Path(resolve_runtime_path(str(ws), "runtime/events/runtime.events.jsonl"))
        sentinel_path = Path(resolve_runtime_path(str(ws), "runtime/events/hp.phases.events.jsonl"))
        state_path = Path(
            resolve_workspace_persistent_path(
                str(ws),
                f"workspace/policy/state_{state.run_id}.json",
            )
        )
        return cls(
            workspace=ws,
            events_path=events_path,
            sentinel_path=sentinel_path,
            state_path=state_path,
            actor=actor,
            state=state,
        )

    def _next_expected_phase(self) -> str:
        next_index = self.state.phase_index + 1
        if next_index >= len(HP_PIPELINE):
            raise PolicyContractError("HP pipeline already completed")
        return HP_PIPELINE[next_index]

    def _current_mode(self) -> str:
        mode_value = str(self.state.meta.get("mode") or "").strip().upper()
        if mode_value in {"S0", "S1", "S2"}:
            return mode_value
        return "S2"

    @staticmethod
    def _snapshot_required_for_mode(mode_value: str, special_handling: bool = False) -> bool:
        if mode_value == "S0" and special_handling:
            return False
        return mode_value in {"S0", "S2"}

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _append_hp_sentinel(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("@@hp " + json.dumps(payload, ensure_ascii=False) + "\n")

    def _advance_phase(self, phase: str, summary: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        expected = self._next_expected_phase()
        normalized = str(phase or "").strip().lower()
        if normalized != expected:
            raise PolicyContractError(f"phase violation: expected '{expected}', got '{normalized or '<empty>'}'")
        self.state.phase_index += 1
        self.state.seq += 1
        event = {
            "ts": _utc_now_iso(),
            "run_id": self.state.run_id,
            "seq": self.state.seq,
            "phase": normalized,
            "type": "phase_transition",
            "status": "ok",
            "actor": self.actor,
            "summary": summary,
        }
        if extra:
            event.update(extra)
        self._append_jsonl(self.events_path, event)
        self._append_hp_sentinel(
            self.sentinel_path,
            {"kind": "phase", "name": normalized, "run_id": self.state.run_id, "status": "ok"},
        )
        self._persist_state()
        return event

    def _persist_state(self) -> None:
        current_phase = None
        if 0 <= self.state.phase_index < len(HP_PIPELINE):
            current_phase = HP_PIPELINE[self.state.phase_index]
        elif self.state.phase_index >= len(HP_PIPELINE):
            current_phase = HP_PIPELINE[-1] if HP_PIPELINE else None
        payload = {
            "run_id": self.state.run_id,
            "phase_index": self.state.phase_index,
            "current_phase": current_phase,
            "approved": self.state.approved,
            "contract_set": self.state.contract_set,
            "blueprint_set": self.state.blueprint_set,
            "snapshot_set": self.state.snapshot_set,
            "verification_set": self.state.verification_set,
            "seq": self.state.seq,
            "meta": self.state.meta,
        }
        _write_json(self.state_path, payload)

    # ── Phase 1: 拟诏 ────────────────────────────────────────────────────────────
    def hp_start_run(self, goal: str, acceptance_criteria: list[str]) -> dict[str, Any]:
        if not str(goal or "").strip():
            raise PolicyContractError("goal is required")
        if not isinstance(acceptance_criteria, list) or not any(str(item).strip() for item in acceptance_criteria):
            raise PolicyContractError("acceptance_criteria must contain at least one non-empty item")
        self.state.contract_set = True
        self.state.meta["goal"] = goal
        self.state.meta["acceptance_criteria"] = acceptance_criteria
        return self._advance_phase("start_run", "Contract accepted", {"goal": goal})

    def hp_set_contract(self, goal: str, acceptance_criteria: list[str]) -> dict[str, Any]:
        return self.hp_start_run(goal, acceptance_criteria)

    # ── Phase 2: 制图 ───────────────────────────────────────────────────────────
    def hp_create_blueprint(
        self,
        blueprint_path: str,
        mode: str,
        budget: dict[str, Any],
        special_handling: bool = False,
    ) -> dict[str, Any]:
        if not self.state.contract_set:
            raise PolicyContractError("contract missing: call hp_start_run first")
        if not str(blueprint_path or "").strip():
            raise PolicyContractError("blueprint_path is required")
        mode_value = str(mode or "").strip().upper()
        if mode_value not in {"S0", "S1", "S2"}:
            raise PolicyContractError("mode must be one of S0/S1/S2")
        self.state.blueprint_set = True
        self.state.meta["blueprint_path"] = blueprint_path
        self.state.meta["mode"] = mode_value
        self.state.meta["budget"] = budget or {}
        self.state.meta["special_handling"] = bool(special_handling)
        return self._advance_phase(
            "blueprint",
            "Blueprint registered",
            {
                "blueprint_path": blueprint_path,
                "mode": mode_value,
                "special_handling": bool(special_handling),
            },
        )

    # ── Phase 3: 廷议 ────────────────────────────────────────────────────────────
    def hp_record_approval(self, approved: bool, ref: str, reason: str = "") -> dict[str, Any]:
        self.state.seq += 1
        self.state.approved = bool(approved)
        status = "approved" if approved else "blocked"
        event = {
            "ts": _utc_now_iso(),
            "run_id": self.state.run_id,
            "seq": self.state.seq,
            "phase": "approval",
            "type": "approval",
            "status": status,
            "actor": self.actor,
            "summary": reason or status,
            "ref": ref,
        }
        self._append_jsonl(self.events_path, event)
        self._persist_state()
        return event

    def hp_phase_transition(self, phase: str, summary: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = str(phase or "").strip().lower()
        if normalized == "policy_check" and not self.state.approved:
            raise PolicyContractError("policy_check blocked: approval missing")
        if normalized == "implementation":
            raise PolicyContractError("implementation phase requires hp_allow_implementation")
        if normalized == "verify" and self.state.phase_index < HP_PIPELINE.index("implementation"):
            raise PolicyContractError("verify blocked: implementation not completed")
        return self._advance_phase(normalized, summary, extra)

    # ── Phase 4: 快照 ───────────────────────────────────────────────────────────
    def hp_create_snapshot(self, snapshot_id: str, snapshot_path: str) -> dict[str, Any]:
        if not str(snapshot_id or "").strip() or not str(snapshot_path or "").strip():
            raise PolicyContractError("snapshot_id and snapshot_path are required")
        self.state.snapshot_set = True
        self.state.meta["snapshot_id"] = snapshot_id
        self.state.meta["snapshot_path"] = snapshot_path
        return self.hp_phase_transition(
            "snapshot",
            "Snapshot recorded",
            {"snapshot_id": snapshot_id, "snapshot_path": snapshot_path},
        )

    # ── Phase 5: 动工 ───────────────────────────────────────────────────────────
    def hp_allow_implementation(
        self,
        implementation_token: str,
        runtime_behavior_change: bool = False,
    ) -> dict[str, Any]:
        token = str(implementation_token or "").strip()
        if not token:
            raise PolicyContractError("implementation_token is required")
        if not self.state.approved:
            raise PolicyContractError("implementation blocked: approval missing")
        if not self.state.blueprint_set:
            raise PolicyContractError("implementation blocked: blueprint missing")

        mode_value = self._current_mode()
        special_handling = bool(self.state.meta.get("special_handling", False))
        requires_snapshot = self._snapshot_required_for_mode(mode_value, special_handling=special_handling)

        if requires_snapshot and not self.state.snapshot_set:
            raise PolicyContractError(f"implementation blocked: snapshot required for mode {mode_value}")

        if not requires_snapshot and not self.state.snapshot_set:
            expected = self._next_expected_phase()
            if expected == "snapshot":
                snapshot_skip_reason = "s1_mode_snapshot_optional"
                if mode_value == "S0" and special_handling:
                    snapshot_skip_reason = "s0_special_handling_snapshot_optional"
                self._advance_phase(
                    "snapshot",
                    "Snapshot skipped for fast lane",
                    {
                        "snapshot_skipped": True,
                        "snapshot_skip_reason": snapshot_skip_reason,
                    },
                )
            self.state.meta["snapshot_skipped"] = True

        self.state.meta["implementation_token_id"] = token
        self.state.meta["runtime_behavior_change"] = bool(runtime_behavior_change)

        expected_impl = self._next_expected_phase()
        if expected_impl != "implementation":
            raise PolicyContractError(f"implementation blocked: expected phase '{expected_impl}'")

        return self._advance_phase(
            "implementation",
            "Implementation authorized",
            {
                "implementation_token_id": token,
                "runtime_behavior_change": bool(runtime_behavior_change),
                "mode": mode_value,
                "snapshot_policy": "required" if requires_snapshot else "optional",
                "special_handling": special_handling,
            },
        )

    def hp_append_evidence(self, artifact_path: str, summary: str, status: str = "ok") -> dict[str, Any]:
        if not str(artifact_path or "").strip():
            raise PolicyContractError("artifact_path is required")
        self.state.seq += 1
        event = {
            "ts": _utc_now_iso(),
            "run_id": self.state.run_id,
            "seq": self.state.seq,
            "phase": "evidence",
            "type": "evidence",
            "status": status,
            "actor": self.actor,
            "summary": summary,
            "artifact_path": artifact_path,
        }
        self._append_jsonl(self.events_path, event)
        self._persist_state()
        return event

    # ── Phase 6: 勘验 ──────────────────────────────────────────────────────────
    def hp_run_verify(
        self,
        nonce: str,
        verification_log: str,
        exit_code: int,
        evidence_run: bool = True,
    ) -> dict[str, Any]:
        if not str(nonce or "").strip():
            raise PolicyContractError("nonce is required")
        if not str(verification_log or "").strip():
            raise PolicyContractError("verification_log is required")
        log_name = Path(verification_log).name
        if not (log_name.startswith("verification_") and log_name.endswith(".log")):
            raise PolicyContractError("verification_log must match verification_<nonce>.log")
        if evidence_run is not True:
            raise PolicyContractError("final verify must be evidence_run=true")
        self.state.verification_set = True
        event = self.hp_phase_transition(
            "verify",
            "Verification recorded",
            {
                "nonce": nonce,
                "artifact_path": verification_log,
                "exit_code": int(exit_code),
                "mode": "evidence_run",
            },
        )
        self.hp_append_evidence(
            verification_log,
            "verification_log",
            "ok" if int(exit_code) == 0 else "failed",
        )
        return event

    # ── Phase 7: 归档 ────────────────────────────────────────────────────────────
    def hp_finalize_run(self, status: str, summary: str) -> dict[str, Any]:
        if not self.state.verification_set:
            raise PolicyContractError("finalize blocked: verification missing")
        return self.hp_phase_transition("finalize", summary, {"final_status": status})


# ── Public service facade ────────────────────────────────────────────────────


class HPProtocolPublicError(RuntimeError):
    """Public error raised by the HP Protocol service layer."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "hp_protocol_error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details) if details else {}


class HPProtocolService:
    """
    Public facade for HP Protocol state machine access.

    All HP Protocol operations are channelled through this service,
    which delegates to the internal PolicyRuntime implementation.
    """

    def __init__(
        self,
        workspace: str,
        run_id: str | None = None,
        actor: str = "Director",
    ) -> None:
        if not str(workspace or "").strip():
            raise HPProtocolPublicError("workspace is required")
        self.workspace = workspace
        self.run_id = run_id
        self.actor = actor

    def _runtime(self) -> PolicyRuntime:
        return PolicyRuntime.create(
            self.workspace,
            run_id=self.run_id,
            actor=self.actor,
        )

    # ── Phase 1 ────────────────────────────────────────────────────────────────
    def start_run(self, goal: str, acceptance_criteria: list[str]) -> dict[str, Any]:
        return self._runtime().hp_start_run(goal, acceptance_criteria)

    def set_contract(self, goal: str, acceptance_criteria: list[str]) -> dict[str, Any]:
        return self.start_run(goal, acceptance_criteria)

    # ── Phase 2 ────────────────────────────────────────────────────────────────
    def create_blueprint(
        self,
        blueprint_path: str,
        mode: str,
        budget: dict[str, Any],
        special_handling: bool = False,
    ) -> dict[str, Any]:
        return self._runtime().hp_create_blueprint(blueprint_path, mode, budget, special_handling=special_handling)

    # ── Phase 3 ────────────────────────────────────────────────────────────────
    def record_approval(self, approved: bool, ref: str, reason: str = "") -> dict[str, Any]:
        return self._runtime().hp_record_approval(approved, ref, reason)

    # ── Phase 4 ───────────────────────────────────────────────────────────────
    def create_snapshot(self, snapshot_id: str, snapshot_path: str) -> dict[str, Any]:
        return self._runtime().hp_create_snapshot(snapshot_id, snapshot_path)

    # ── Phase 5 ────────────────────────────────────────────────────────────────
    def allow_implementation(self, implementation_token: str, runtime_behavior_change: bool = False) -> dict[str, Any]:
        return self._runtime().hp_allow_implementation(
            implementation_token, runtime_behavior_change=runtime_behavior_change
        )

    # ── Phase 6 ────────────────────────────────────────────────────────────────
    def run_verify(
        self,
        nonce: str,
        verification_log: str,
        exit_code: int,
        evidence_run: bool = True,
    ) -> dict[str, Any]:
        return self._runtime().hp_run_verify(nonce, verification_log, exit_code, evidence_run)

    # ── Phase 7 ────────────────────────────────────────────────────────────────
    def finalize_run(self, status: str, summary: str) -> dict[str, Any]:
        return self._runtime().hp_finalize_run(status, summary)


__all__ = [
    "HP_PIPELINE",
    "HPProtocolPublicError",
    "HPProtocolService",
    "PolicyContractError",
    "PolicyRunState",
    "PolicyRuntime",
]
