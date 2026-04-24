from __future__ import annotations

import logging
import os
import uuid
from datetime import timedelta
from fnmatch import fnmatch
from typing import Any

import yaml
from polaris.cells.context.engine.public.service import get_anthropomorphic_context_v2
from polaris.cells.roles.session.public import (
    RoleSessionContextMemoryService,
    RoleSessionService,
)
from polaris.domain.cognitive_runtime import (
    ChangeSetValidationResult,
    ContextHandoffPack,
    ContextSnapshot,
    DiffCellMapping,
    EditScopeLease,
    HandoffRehydration,
    ProjectionCompileRequest,
    PromotionDecisionRecord,
    RollbackLedgerEntry,
    RuntimeReceipt,
    TurnEnvelope,
)
from polaris.domain.verification.impact_analyzer import ImpactAnalyzer
from polaris.domain.verification.write_gate import WriteGate
from polaris.infrastructure.cognitive_runtime import CognitiveRuntimeSqliteStore
from polaris.kernelone.utils.time_utils import utc_now as _utc_now

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return _utc_now().replace(microsecond=0).isoformat()


def _normalize_paths(paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in paths:
        token = str(value or "").strip()
        if not token:
            continue
        item = os.path.normpath(token).replace("\\", "/")
        if item not in normalized:
            normalized.append(item)
    return tuple(normalized)


def _normalize_rel_path(workspace: str, path: str) -> str:
    workspace_root = os.path.abspath(workspace)
    token = os.path.normpath(str(path or "").strip()).replace("\\", "/")
    if not token:
        return ""
    if os.path.isabs(token):
        abs_path = os.path.normpath(token)
        try:
            rel = os.path.relpath(abs_path, workspace_root)
            return os.path.normpath(rel).replace("\\", "/")
        except ValueError:
            return token
    return token.lstrip("./")


def _owned_path_matches(path: str, pattern: str) -> bool:
    candidate = str(path or "").strip().replace("\\", "/")
    token = str(pattern or "").strip().replace("\\", "/")
    if not candidate or not token:
        return False
    if token.endswith("/**"):
        prefix = token[:-3].rstrip("/")
        return candidate == prefix or candidate.startswith(prefix + "/")
    if any(ch in token for ch in "*?["):
        return fnmatch(candidate, token)
    return candidate == token


def _resolve_workspace_bounded_path(workspace: str, candidate_path: str) -> str:
    workspace_root = os.path.abspath(str(workspace or "").strip())
    if not workspace_root:
        raise ValueError("workspace is required")
    token = str(candidate_path or "").strip()
    if not token:
        raise ValueError("graph_catalog_path is required")
    resolved = os.path.abspath(os.path.join(workspace_root, token))
    try:
        common = os.path.commonpath([workspace_root, resolved])
    except ValueError as exc:
        raise ValueError("graph_catalog_path must stay within workspace") from exc
    if common != workspace_root:
        raise ValueError("graph_catalog_path must stay within workspace")
    return resolved


def _load_graph_cells(workspace: str, graph_catalog_path: str) -> list[dict[str, Any]]:
    catalog_path = _resolve_workspace_bounded_path(workspace, graph_catalog_path)
    with open(catalog_path, encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}
    cells = payload.get("cells")
    if not isinstance(cells, list):
        return []
    return [dict(item) for item in cells if isinstance(item, dict)]


def _normalize_turn_envelope(
    payload: dict[str, Any] | None,
) -> TurnEnvelope | None:
    return TurnEnvelope.from_mapping(dict(payload or {}))


def _normalize_mapping_sequence(payload: object) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(payload, list):
        return ()
    for item in payload:
        if isinstance(item, dict):
            normalized.append(dict(item))
    return tuple(normalized)


def _dedupe_strings(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        token = str(value or "").strip()
        if token and token not in result:
            result.append(token)
    return tuple(result)


class CognitiveRuntimeService:
    """Cross-role authority facade on top of Context OS and session truth."""

    def __init__(
        self,
        *,
        session_service: RoleSessionService | None = None,
        context_memory_service: RoleSessionContextMemoryService | None = None,
        store: CognitiveRuntimeSqliteStore | None = None,
    ) -> None:
        self._session_service = session_service
        self._context_memory_service = context_memory_service
        self._store = store
        self._store_workspace_key: str | None = None
        self._stores: dict[str, CognitiveRuntimeSqliteStore] = {}

    @staticmethod
    def _workspace_key(workspace: str) -> str:
        token = str(workspace or "").strip()
        if not token:
            raise ValueError("workspace is required")
        return os.path.normcase(os.path.abspath(token))

    @property
    def session_service(self) -> RoleSessionService:
        if self._session_service is None:
            self._session_service = RoleSessionService()
        return self._session_service

    @property
    def context_memory_service(self) -> RoleSessionContextMemoryService:
        if self._context_memory_service is None:
            self._context_memory_service = RoleSessionContextMemoryService(session_service=self.session_service)
        return self._context_memory_service

    def _store_for(self, workspace: str) -> CognitiveRuntimeSqliteStore:
        key = self._workspace_key(workspace)
        if self._store is not None:
            if self._store_workspace_key is None:
                self._store_workspace_key = key
            elif self._store_workspace_key != key:
                raise ValueError("Injected Cognitive Runtime store is bound to a different workspace")
            return self._store
        store = self._stores.get(key)
        if store is None:
            store = CognitiveRuntimeSqliteStore(workspace)
            self._stores[key] = store
        return store

    def close(self) -> None:
        if self._context_memory_service is not None:
            self._context_memory_service.close()
            self._context_memory_service = None
        if self._session_service is not None:
            self._session_service.close()
            self._session_service = None
        if self._store is not None:
            self._store.close()
            self._store = None
            self._store_workspace_key = None
        for store in self._stores.values():
            store.close()
        self._stores.clear()

    def resolve_context(
        self,
        *,
        workspace: str,
        role: str,
        query: str,
        step: int,
        run_id: str,
        mode: str,
        session_id: str | None = None,
        events_path: str = "",
        sources_enabled: list[str] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> ContextSnapshot:
        context_override = self.session_service.get_context_config_dict(session_id) if session_id else None
        bundle = get_anthropomorphic_context_v2(
            workspace,
            role,
            query,
            step,
            run_id,
            mode,
            events_path=events_path,
            sources_enabled=sources_enabled,
            policy=policy,
            context_override=context_override,
            session_id=session_id or "",
        )
        pack = bundle["context_pack"]
        return ContextSnapshot(
            workspace=workspace,
            role=role,
            query=query,
            run_id=run_id,
            step=int(step),
            mode=mode,
            session_id=session_id,
            rendered_prompt=str(bundle["anthropomorphic_context"] or ""),
            token_usage_estimate=int(getattr(pack, "total_tokens", 0) or 0),
            source_refs=tuple(
                str(getattr(item, "id", ""))
                for item in list(getattr(pack, "items", []))
                if str(getattr(item, "id", "")).strip()
            ),
            context_os_summary=dict(bundle.get("context_os_summary") or {}),
        )

    def lease_edit_scope(
        self,
        *,
        workspace: str,
        requested_by: str,
        scope_paths: list[str] | tuple[str, ...],
        ttl_seconds: int = 1800,
        session_id: str | None = None,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> EditScopeLease:
        normalized_scope = _normalize_paths(list(scope_paths))
        if not normalized_scope:
            raise ValueError("scope_paths must contain at least one path")
        run_card = self.context_memory_service.get_state_for_session(session_id, "run_card") if session_id else None
        current_goal = ""
        hard_constraints: tuple[str, ...] = ()
        if isinstance(run_card, dict):
            current_goal = str(run_card.get("current_goal") or "").strip()
            hard_constraints = tuple(
                str(item) for item in (run_card.get("hard_constraints") or []) if str(item).strip()
            )
        issued_at = _utc_now()
        expires_at = issued_at + timedelta(seconds=max(60, int(ttl_seconds)))
        return EditScopeLease(
            lease_id=str(uuid.uuid4()),
            workspace=workspace,
            requested_by=requested_by,
            scope_paths=normalized_scope,
            issued_at=issued_at.replace(microsecond=0).isoformat(),
            expires_at=expires_at.replace(microsecond=0).isoformat(),
            reason=str(reason or "").strip(),
            session_id=session_id,
            current_goal=current_goal,
            hard_constraints=hard_constraints,
            metadata=dict(metadata or {}),
        )

    def validate_change_set(
        self,
        *,
        workspace: str,
        changed_files: list[str] | tuple[str, ...],
        allowed_scope_paths: list[str] | tuple[str, ...],
        evidence_refs: list[str] | tuple[str, ...] | None = None,
        require_change: bool = True,
    ) -> ChangeSetValidationResult:
        normalized_changed = _normalize_paths(list(changed_files))
        normalized_scope = _normalize_paths(list(allowed_scope_paths))
        gate = WriteGate.validate(
            changed_files=list(normalized_changed),
            act_files=list(normalized_changed),
            pm_target_files=list(normalized_scope),
            require_change=require_change,
        )
        impact = ImpactAnalyzer(workspace).analyze(list(normalized_changed))
        reasons = list(impact.reasons)
        if gate.reason and gate.reason not in reasons:
            reasons.append(gate.reason)
        if require_change and not normalized_changed and "No files were changed" not in reasons:
            reasons.append("No files were changed")
        return ChangeSetValidationResult(
            validation_id=str(uuid.uuid4()),
            workspace=workspace,
            changed_files=normalized_changed,
            allowed_scope_paths=normalized_scope,
            write_gate_allowed=bool(gate.allowed),
            impact_score=int(impact.score),
            risk_level=str(impact.level.value),
            reasons=tuple(reasons),
            recommendations=tuple(impact.recommendations),
            extra_files=tuple(gate.extra_files or []),
            evidence_refs=tuple(str(item) for item in (evidence_refs or []) if str(item).strip()),
        )

    def record_runtime_receipt(
        self,
        *,
        workspace: str,
        receipt_type: str,
        payload: dict[str, Any] | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        trace_refs: list[str] | tuple[str, ...] | None = None,
        turn_envelope: dict[str, Any] | None = None,
    ) -> RuntimeReceipt:
        normalized_payload = dict(payload or {})
        legacy_envelope = normalized_payload.pop("turn_envelope", None)
        resolved_envelope = _normalize_turn_envelope(turn_envelope or legacy_envelope)
        receipt_id = str(uuid.uuid4())
        if resolved_envelope is not None:
            resolved_envelope = resolved_envelope.with_receipt_ids((receipt_id,))
        receipt = RuntimeReceipt(
            receipt_id=receipt_id,
            receipt_type=str(receipt_type or "").strip(),
            workspace=workspace,
            created_at=_iso_now(),
            payload=normalized_payload,
            session_id=session_id,
            run_id=run_id,
            trace_refs=tuple(str(item) for item in (trace_refs or []) if str(item).strip()),
            turn_envelope=resolved_envelope,
        )
        return self._store_for(workspace).append_receipt(receipt)

    def get_runtime_receipt(
        self,
        *,
        workspace: str,
        receipt_id: str,
    ) -> RuntimeReceipt | None:
        return self._store_for(workspace).get_receipt(receipt_id)

    def export_handoff_pack(
        self,
        *,
        workspace: str,
        session_id: str,
        run_id: str | None = None,
        reason: str = "",
        receipt_limit: int = 20,
        turn_envelope: dict[str, Any] | None = None,
    ) -> ContextHandoffPack:
        run_card = self.context_memory_service.get_state_for_session(session_id, "run_card")
        current_goal = ""
        hard_constraints: tuple[str, ...] = ()
        open_loops: tuple[str, ...] = ()
        run_card_payload: dict[str, Any] = {}
        decision_log_payload: tuple[dict[str, Any], ...] = ()
        context_slice_plan_payload: dict[str, Any] = {}
        state_snapshot: dict[str, Any] = {}
        if isinstance(run_card, dict):
            current_goal = str(run_card.get("current_goal") or "").strip()
            hard_constraints = tuple(
                str(item) for item in (run_card.get("hard_constraints") or []) if str(item).strip()
            )
            open_loops = tuple(str(item) for item in (run_card.get("open_loops") or []) if str(item).strip())
            run_card_payload = dict(run_card)
            state_snapshot["run_card"] = dict(run_card)

        slice_plan = self.context_memory_service.get_state_for_session(
            session_id,
            "context_slice_plan",
        )
        if isinstance(slice_plan, dict):
            context_slice_plan_payload = dict(slice_plan)
            state_snapshot["context_slice_plan"] = dict(slice_plan)

        decision_log = self.context_memory_service.get_state_for_session(
            session_id,
            "decision_log",
        )
        if isinstance(decision_log, list):
            decision_log_payload = _normalize_mapping_sequence(decision_log)
            if decision_log_payload:
                state_snapshot["decision_log"] = [dict(item) for item in decision_log_payload]

        receipts = self._store_for(workspace).list_receipts(
            session_id=session_id,
            run_id=run_id,
            limit=receipt_limit,
        )
        receipt_refs = tuple(receipt.receipt_id for receipt in receipts)
        resolved_envelope = _normalize_turn_envelope(turn_envelope)
        if resolved_envelope is None:
            for receipt in receipts:
                if receipt.turn_envelope is not None:
                    resolved_envelope = receipt.turn_envelope
                    break
        if resolved_envelope is not None:
            resolved_envelope = resolved_envelope.with_receipt_ids(receipt_refs)
        memory_hits = self.context_memory_service.search_memory_for_session(
            session_id,
            current_goal or reason or "handoff",
            limit=6,
        )
        artifact_refs = tuple(
            str(item.get("artifact_id") or item.get("id") or "")
            for item in memory_hits
            if isinstance(item, dict) and str(item.get("artifact_id") or item.get("id") or "").strip()
        )
        episode_refs = tuple(
            str(item.get("episode_id") or item.get("id") or "")
            for item in memory_hits
            if isinstance(item, dict) and str(item.get("episode_id") or item.get("id") or "").strip()
        )
        source_spans: list[str] = []
        for episode_id in episode_refs:
            episode_payload = self.context_memory_service.read_episode_for_session(session_id, episode_id)
            if not isinstance(episode_payload, dict):
                continue
            for item in episode_payload.get("source_spans") or []:
                token = str(item or "").strip()
                if token and token not in source_spans:
                    source_spans.append(token)
        handoff = ContextHandoffPack(
            handoff_id=str(uuid.uuid4()),
            workspace=workspace,
            created_at=_iso_now(),
            session_id=session_id,
            run_id=run_id,
            reason=str(reason or "").strip(),
            current_goal=current_goal,
            hard_constraints=hard_constraints,
            open_loops=open_loops,
            run_card=run_card_payload,
            context_slice_plan=context_slice_plan_payload,
            decision_log=decision_log_payload,
            artifact_refs=artifact_refs,
            episode_refs=episode_refs,
            receipt_refs=receipt_refs,
            source_spans=tuple(source_spans),
            state_snapshot=state_snapshot,
            turn_envelope=resolved_envelope,
        )
        return self._store_for(workspace).save_handoff_pack(handoff)

    def get_handoff_pack(
        self,
        *,
        workspace: str,
        handoff_id: str,
    ) -> ContextHandoffPack | None:
        return self._store_for(workspace).get_handoff_pack(handoff_id)

    def rehydrate_handoff_pack(
        self,
        *,
        workspace: str,
        handoff_id: str,
        target_role: str,
        target_session_id: str | None = None,
    ) -> HandoffRehydration:
        handoff = self.get_handoff_pack(workspace=workspace, handoff_id=handoff_id)
        if handoff is None:
            raise ValueError(f"handoff pack not found: {handoff_id}")

        run_card = dict(handoff.run_card or {})
        if not run_card and isinstance(handoff.state_snapshot.get("run_card"), dict):
            run_card = dict(handoff.state_snapshot.get("run_card") or {})
        if handoff.current_goal and not str(run_card.get("current_goal") or "").strip():
            run_card["current_goal"] = handoff.current_goal
        if handoff.hard_constraints and not isinstance(run_card.get("hard_constraints"), list):
            run_card["hard_constraints"] = list(handoff.hard_constraints)
        if handoff.open_loops and not isinstance(run_card.get("open_loops"), list):
            run_card["open_loops"] = list(handoff.open_loops)
        if handoff.artifact_refs and not isinstance(run_card.get("active_artifacts"), list):
            run_card["active_artifacts"] = list(handoff.artifact_refs)

        context_slice_plan = dict(handoff.context_slice_plan or {})
        if not context_slice_plan and isinstance(handoff.state_snapshot.get("context_slice_plan"), dict):
            context_slice_plan = dict(handoff.state_snapshot.get("context_slice_plan") or {})
        if not context_slice_plan:
            included: list[dict[str, str]] = []
            if run_card:
                included.append({"type": "state", "ref": "run_card", "reason": "handoff_root"})
            if handoff.decision_log:
                included.append({"type": "state", "ref": "decision_log", "reason": "handoff_decisions"})
            if handoff.artifact_refs:
                included.append(
                    {
                        "type": "artifact",
                        "ref": handoff.artifact_refs[0],
                        "reason": "handoff_artifact_ref",
                    }
                )
            context_slice_plan = {
                "plan_id": f"handoff-rehydrate-{handoff.handoff_id}",
                "budget_tokens": 0,
                "roots": ["handoff_pack", "run_card", "open_loops"],
                "included": included,
                "excluded": [],
                "pressure_level": "normal",
            }

        decision_log = handoff.decision_log
        if not decision_log:
            decision_log = _normalize_mapping_sequence(handoff.state_snapshot.get("decision_log"))
        source_spans = _dedupe_strings(list(handoff.source_spans))
        state_first_context_os = {
            "mode": "state_first_context_os.handoff_rehydrate",
            "adapter_id": str(target_role or "").strip().lower() or "generic",
            "handoff_id": handoff.handoff_id,
            "source_session_id": handoff.session_id,
            "source_run_id": handoff.run_id,
            "reason": handoff.reason,
            "run_card": run_card,
            "context_slice_plan": context_slice_plan,
            "decision_log": [dict(item) for item in decision_log],
            "active_artifacts": list(handoff.artifact_refs),
            "artifact_stubs": [
                {
                    "artifact_id": ref,
                    "type": "handoff_ref",
                    "mime": "application/x-handoff-ref",
                    "restore_tool": "read_artifact",
                    "peek": f"handoff artifact ref: {ref}",
                }
                for ref in handoff.artifact_refs
            ],
            "source_spans": list(source_spans),
            "turn_envelope": (handoff.turn_envelope.to_dict() if handoff.turn_envelope is not None else None),
        }
        metadata_patch = {
            "handoff_id": handoff.handoff_id,
            "handoff_source_session_id": handoff.session_id,
            "handoff_source_run_id": handoff.run_id,
            "handoff_rehydrated": True,
        }
        if source_spans:
            metadata_patch["handoff_source_spans"] = list(source_spans)  # type: ignore[assignment]
        return HandoffRehydration(
            rehydration_id=str(uuid.uuid4()),
            handoff_id=handoff.handoff_id,
            workspace=workspace,
            created_at=_iso_now(),
            target_role=str(target_role or "").strip(),
            target_session_id=target_session_id,
            current_goal=str(run_card.get("current_goal") or handoff.current_goal or "").strip(),
            hard_constraints=_dedupe_strings(list(handoff.hard_constraints)),
            open_loops=_dedupe_strings(list(handoff.open_loops)),
            run_card=run_card,
            context_slice_plan=context_slice_plan,
            decision_log=decision_log,
            artifact_refs=_dedupe_strings(list(handoff.artifact_refs)),
            episode_refs=_dedupe_strings(list(handoff.episode_refs)),
            receipt_refs=_dedupe_strings(list(handoff.receipt_refs)),
            source_spans=source_spans,
            context_override={
                "state_first_context_os": state_first_context_os,
                "cognitive_runtime_handoff": {
                    "handoff_id": handoff.handoff_id,
                    "source_session_id": handoff.session_id,
                    "source_run_id": handoff.run_id,
                    "reason": handoff.reason,
                    "artifact_refs": list(handoff.artifact_refs),
                    "episode_refs": list(handoff.episode_refs),
                    "receipt_refs": list(handoff.receipt_refs),
                    "source_spans": list(source_spans),
                },
            },
            metadata_patch=metadata_patch,
            turn_envelope=handoff.turn_envelope,
        )

    def map_diff_to_cells(
        self,
        *,
        workspace: str,
        changed_files: list[str] | tuple[str, ...],
        graph_catalog_path: str = "docs/graph/catalog/cells.yaml",
    ) -> DiffCellMapping:
        normalized_changed = tuple(
            _normalize_rel_path(workspace, path) for path in _normalize_paths(list(changed_files))
        )
        normalized_changed = tuple(item for item in normalized_changed if item)
        file_to_cells: dict[str, tuple[str, ...]] = {}
        matched_cells: list[str] = []
        notes: list[str] = []
        try:
            cells = _load_graph_cells(workspace, graph_catalog_path)
        except ValueError:
            # Path-boundary validation errors are security-significant and must
            # not be downgraded into "catalog unavailable" soft notes.
            raise
        except RuntimeError:
            # SECURITY FIX (P2-014): Log at warning level for audit trail.
            logger.warning(
                "Graph catalog unavailable for diff mapping: workspace=%s catalog=%s", workspace, graph_catalog_path
            )
            cells = []
            notes.append("graph_catalog_unavailable")
        if not cells:
            notes.append("graph_catalog_empty_or_invalid")
        for changed in normalized_changed:
            owners: list[str] = []
            for cell in cells:
                cell_id = str(cell.get("id") or "").strip()
                owned_paths = cell.get("owned_paths")
                if not cell_id or not isinstance(owned_paths, list):
                    continue
                if any(_owned_path_matches(changed, str(pattern)) for pattern in owned_paths):
                    owners.append(cell_id)
                    if cell_id not in matched_cells:
                        matched_cells.append(cell_id)
            file_to_cells[changed] = tuple(owners)
        unmapped = tuple(path for path, owners in file_to_cells.items() if not owners)
        if unmapped:
            notes.append("unmapped_files_present")
        mapping = DiffCellMapping(
            mapping_id=str(uuid.uuid4()),
            workspace=workspace,
            created_at=_iso_now(),
            graph_catalog_path=graph_catalog_path,
            changed_files=normalized_changed,
            matched_cells=tuple(matched_cells),
            unmapped_files=unmapped,
            file_to_cells=file_to_cells,
            notes=tuple(notes),
        )
        return self._store_for(workspace).append_diff_mapping(mapping)

    def request_projection_compile(
        self,
        *,
        workspace: str,
        requested_by: str,
        subject_ref: str,
        changed_files: list[str] | tuple[str, ...],
        mapped_cells: list[str] | tuple[str, ...],
        session_id: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProjectionCompileRequest:
        normalized_changed = tuple(
            _normalize_rel_path(workspace, path) for path in _normalize_paths(list(changed_files))
        )
        normalized_cells = _normalize_paths(list(mapped_cells))
        status = "queued" if normalized_cells else "rejected"
        request = ProjectionCompileRequest(
            request_id=str(uuid.uuid4()),
            workspace=workspace,
            created_at=_iso_now(),
            requested_by=str(requested_by or "").strip(),
            subject_ref=str(subject_ref or "").strip(),
            status=status,
            changed_files=tuple(item for item in normalized_changed if item),
            mapped_cells=normalized_cells,
            session_id=session_id,
            run_id=run_id,
            metadata=dict(metadata or {}),
        )
        stored = self._store_for(workspace).append_projection_request(request)
        self.record_runtime_receipt(
            workspace=workspace,
            receipt_type="projection_compile_requested",
            payload={
                "request_id": stored.request_id,
                "subject_ref": stored.subject_ref,
                "status": stored.status,
                "mapped_cells": list(stored.mapped_cells),
            },
            session_id=session_id,
            run_id=run_id,
        )
        return stored

    def promote_or_reject(
        self,
        *,
        workspace: str,
        subject_ref: str,
        changed_files: list[str] | tuple[str, ...],
        mapped_cells: list[str] | tuple[str, ...],
        write_gate_allowed: bool,
        projection_status: str,
        projection_request_id: str | None = None,
        receipt_refs: list[str] | tuple[str, ...] | None = None,
        reasons: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PromotionDecisionRecord:
        normalized_changed = tuple(
            _normalize_rel_path(workspace, path) for path in _normalize_paths(list(changed_files))
        )
        normalized_cells = _normalize_paths(list(mapped_cells))
        normalized_reasons = [str(item).strip() for item in (reasons or []) if str(item).strip()]
        decision = "promote"
        if not write_gate_allowed:
            decision = "reject"
            normalized_reasons.append("write_gate_not_allowed")
        if not normalized_cells:
            decision = "reject"
            normalized_reasons.append("no_mapped_cells")
        if str(projection_status or "").strip().lower() not in {"queued", "compiled", "ready"}:
            decision = "reject"
            normalized_reasons.append("projection_not_ready")
        if not normalized_changed:
            decision = "reject"
            normalized_reasons.append("no_changed_files")

        record = PromotionDecisionRecord(
            decision_id=str(uuid.uuid4()),
            workspace=workspace,
            created_at=_iso_now(),
            subject_ref=str(subject_ref or "").strip(),
            decision=decision,
            reasons=tuple(dict.fromkeys(normalized_reasons)),
            mapped_cells=normalized_cells,
            changed_files=tuple(item for item in normalized_changed if item),
            projection_request_id=(
                str(projection_request_id).strip() if str(projection_request_id or "").strip() else None
            ),
            receipt_refs=tuple(str(item) for item in (receipt_refs or []) if str(item).strip()),
            metadata=dict(metadata or {}),
        )
        stored = self._store_for(workspace).append_promotion_decision(record)
        self.record_runtime_receipt(
            workspace=workspace,
            receipt_type="promotion_decision",
            payload={
                "decision_id": stored.decision_id,
                "subject_ref": stored.subject_ref,
                "decision": stored.decision,
                "reasons": list(stored.reasons),
                "mapped_cells": list(stored.mapped_cells),
            },
        )
        return stored

    def record_rollback_ledger(
        self,
        *,
        workspace: str,
        subject_ref: str,
        reason: str,
        decision_id: str | None = None,
        changed_files: list[str] | tuple[str, ...] | None = None,
        receipt_refs: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RollbackLedgerEntry:
        normalized_changed = tuple(
            _normalize_rel_path(workspace, path) for path in _normalize_paths(list(changed_files or ()))
        )
        entry = RollbackLedgerEntry(
            rollback_id=str(uuid.uuid4()),
            workspace=workspace,
            created_at=_iso_now(),
            subject_ref=str(subject_ref or "").strip(),
            reason=str(reason or "").strip(),
            decision_id=str(decision_id or "").strip() or None,
            changed_files=tuple(item for item in normalized_changed if item),
            receipt_refs=tuple(str(item) for item in (receipt_refs or []) if str(item).strip()),
            metadata=dict(metadata or {}),
        )
        stored = self._store_for(workspace).append_rollback_ledger_entry(entry)
        self.record_runtime_receipt(
            workspace=workspace,
            receipt_type="rollback_ledger_recorded",
            payload={
                "rollback_id": stored.rollback_id,
                "subject_ref": stored.subject_ref,
                "decision_id": stored.decision_id,
                "reason": stored.reason,
            },
        )
        return stored
