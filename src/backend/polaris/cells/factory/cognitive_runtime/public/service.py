from __future__ import annotations

from polaris.application.cognitive_runtime import CognitiveRuntimeService
from polaris.cells.factory.cognitive_runtime.public.contracts import (
    DiffCellMappingResultV1,
    ExportHandoffPackCommandV1,
    GetHandoffPackQueryV1,
    GetRuntimeReceiptQueryV1,
    HandoffPackResultV1,
    HandoffRehydrationResultV1,
    LeaseEditScopeCommandV1,
    LeaseEditScopeResultV1,
    MapDiffToCellsCommandV1,
    ProjectionCompileResultV1,
    PromoteOrRejectCommandV1,
    PromotionDecisionResultV1,
    RecordRollbackLedgerCommandV1,
    RecordRuntimeReceiptCommandV1,
    RehydrateHandoffPackCommandV1,
    RequestProjectionCompileCommandV1,
    ResolveContextCommandV1,
    ResolveContextResultV1,
    RollbackLedgerResultV1,
    RuntimeReceiptResultV1,
    ValidateChangeSetCommandV1,
    ValidateChangeSetResultV1,
)
from polaris.kernelone.context.runtime_feature_flags import (
    CognitiveRuntimeMode,
    resolve_cognitive_runtime_mode,
)


class CognitiveRuntimePublicService:
    """Public cell facade for cross-role runtime authority operations."""

    def __init__(self, runtime: CognitiveRuntimeService | None = None) -> None:
        self._runtime = runtime or CognitiveRuntimeService()

    def close(self) -> None:
        self._runtime.close()

    @staticmethod
    def _mode() -> CognitiveRuntimeMode:
        return resolve_cognitive_runtime_mode()

    def resolve_context(self, command: ResolveContextCommandV1) -> ResolveContextResultV1:
        if self._mode() is CognitiveRuntimeMode.OFF:
            return ResolveContextResultV1(
                ok=False,
                error_code="cognitive_runtime_disabled",
                error_message="Cognitive Runtime is disabled by runtime mode.",
            )
        try:
            snapshot = self._runtime.resolve_context(
                workspace=command.workspace,
                role=command.role,
                query=command.query,
                step=command.step,
                run_id=command.run_id,
                mode=command.mode,
                session_id=command.session_id,
                events_path=command.events_path,
                sources_enabled=list(command.sources_enabled),
                policy=dict(command.policy),
            )
        except (RuntimeError, ValueError) as exc:
            return ResolveContextResultV1(
                ok=False,
                error_code="resolve_context_failed",
                error_message=str(exc),
            )
        return ResolveContextResultV1(ok=True, snapshot=snapshot)

    def lease_edit_scope(self, command: LeaseEditScopeCommandV1) -> LeaseEditScopeResultV1:
        if self._mode() is CognitiveRuntimeMode.OFF:
            return LeaseEditScopeResultV1(
                ok=False,
                error_code="cognitive_runtime_disabled",
                error_message="Cognitive Runtime is disabled by runtime mode.",
            )
        try:
            lease = self._runtime.lease_edit_scope(
                workspace=command.workspace,
                requested_by=command.requested_by,
                scope_paths=list(command.scope_paths),
                ttl_seconds=command.ttl_seconds,
                session_id=command.session_id,
                reason=command.reason,
                metadata=dict(command.metadata),
            )
        except (RuntimeError, ValueError) as exc:
            return LeaseEditScopeResultV1(
                ok=False,
                error_code="lease_edit_scope_failed",
                error_message=str(exc),
            )
        return LeaseEditScopeResultV1(ok=True, lease=lease)

    def validate_change_set(self, command: ValidateChangeSetCommandV1) -> ValidateChangeSetResultV1:
        if self._mode() is CognitiveRuntimeMode.OFF:
            return ValidateChangeSetResultV1(
                ok=False,
                error_code="cognitive_runtime_disabled",
                error_message="Cognitive Runtime is disabled by runtime mode.",
            )
        try:
            validation = self._runtime.validate_change_set(
                workspace=command.workspace,
                changed_files=list(command.changed_files),
                allowed_scope_paths=list(command.allowed_scope_paths),
                evidence_refs=list(command.evidence_refs),
                require_change=command.require_change,
            )
        except (RuntimeError, ValueError) as exc:
            return ValidateChangeSetResultV1(
                ok=False,
                error_code="validate_change_set_failed",
                error_message=str(exc),
            )
        return ValidateChangeSetResultV1(ok=validation.ok, validation=validation)

    def record_runtime_receipt(self, command: RecordRuntimeReceiptCommandV1) -> RuntimeReceiptResultV1:
        if self._mode() is CognitiveRuntimeMode.OFF:
            return RuntimeReceiptResultV1(
                ok=False,
                error_code="cognitive_runtime_disabled",
                error_message="Cognitive Runtime is disabled by runtime mode.",
            )
        try:
            receipt = self._runtime.record_runtime_receipt(
                workspace=command.workspace,
                receipt_type=command.receipt_type,
                payload=dict(command.payload),
                session_id=command.session_id,
                run_id=command.run_id,
                trace_refs=list(command.trace_refs),
                turn_envelope=dict(command.turn_envelope),
            )
        except (RuntimeError, ValueError) as exc:
            return RuntimeReceiptResultV1(
                ok=False,
                error_code="record_runtime_receipt_failed",
                error_message=str(exc),
            )
        return RuntimeReceiptResultV1(ok=True, receipt=receipt)

    def export_handoff_pack(self, command: ExportHandoffPackCommandV1) -> HandoffPackResultV1:
        if self._mode() is CognitiveRuntimeMode.OFF:
            return HandoffPackResultV1(
                ok=False,
                error_code="cognitive_runtime_disabled",
                error_message="Cognitive Runtime is disabled by runtime mode.",
            )
        try:
            handoff = self._runtime.export_handoff_pack(
                workspace=command.workspace,
                session_id=command.session_id,
                run_id=command.run_id,
                reason=command.reason,
                receipt_limit=command.receipt_limit,
                turn_envelope=dict(command.turn_envelope),
            )
        except (RuntimeError, ValueError) as exc:
            return HandoffPackResultV1(
                ok=False,
                error_code="export_handoff_pack_failed",
                error_message=str(exc),
            )
        return HandoffPackResultV1(ok=True, handoff=handoff)

    def map_diff_to_cells(self, command: MapDiffToCellsCommandV1) -> DiffCellMappingResultV1:
        if self._mode() is CognitiveRuntimeMode.OFF:
            return DiffCellMappingResultV1(
                ok=False,
                error_code="cognitive_runtime_disabled",
                error_message="Cognitive Runtime is disabled by runtime mode.",
            )
        try:
            mapping = self._runtime.map_diff_to_cells(
                workspace=command.workspace,
                changed_files=list(command.changed_files),
                graph_catalog_path=command.graph_catalog_path,
            )
        except (RuntimeError, ValueError) as exc:
            return DiffCellMappingResultV1(
                ok=False,
                error_code="map_diff_to_cells_failed",
                error_message=str(exc),
            )
        return DiffCellMappingResultV1(ok=True, mapping=mapping)

    def request_projection_compile(
        self,
        command: RequestProjectionCompileCommandV1,
    ) -> ProjectionCompileResultV1:
        if self._mode() is CognitiveRuntimeMode.OFF:
            return ProjectionCompileResultV1(
                ok=False,
                error_code="cognitive_runtime_disabled",
                error_message="Cognitive Runtime is disabled by runtime mode.",
            )
        try:
            request = self._runtime.request_projection_compile(
                workspace=command.workspace,
                requested_by=command.requested_by,
                subject_ref=command.subject_ref,
                changed_files=list(command.changed_files),
                mapped_cells=list(command.mapped_cells),
                session_id=command.session_id,
                run_id=command.run_id,
                metadata=dict(command.metadata),
            )
        except (RuntimeError, ValueError) as exc:
            return ProjectionCompileResultV1(
                ok=False,
                error_code="request_projection_compile_failed",
                error_message=str(exc),
            )
        return ProjectionCompileResultV1(ok=True, request=request)

    def promote_or_reject(
        self,
        command: PromoteOrRejectCommandV1,
    ) -> PromotionDecisionResultV1:
        if self._mode() is CognitiveRuntimeMode.OFF:
            return PromotionDecisionResultV1(
                ok=False,
                error_code="cognitive_runtime_disabled",
                error_message="Cognitive Runtime is disabled by runtime mode.",
            )
        try:
            decision = self._runtime.promote_or_reject(
                workspace=command.workspace,
                subject_ref=command.subject_ref,
                changed_files=list(command.changed_files),
                mapped_cells=list(command.mapped_cells),
                write_gate_allowed=command.write_gate_allowed,
                projection_status=command.projection_status,
                projection_request_id=command.projection_request_id,
                receipt_refs=list(command.receipt_refs),
                reasons=list(command.reasons),
                metadata=dict(command.metadata),
            )
        except (RuntimeError, ValueError) as exc:
            return PromotionDecisionResultV1(
                ok=False,
                error_code="promote_or_reject_failed",
                error_message=str(exc),
            )
        return PromotionDecisionResultV1(ok=decision.decision == "promote", decision=decision)

    def record_rollback_ledger(
        self,
        command: RecordRollbackLedgerCommandV1,
    ) -> RollbackLedgerResultV1:
        if self._mode() is CognitiveRuntimeMode.OFF:
            return RollbackLedgerResultV1(
                ok=False,
                error_code="cognitive_runtime_disabled",
                error_message="Cognitive Runtime is disabled by runtime mode.",
            )
        try:
            entry = self._runtime.record_rollback_ledger(
                workspace=command.workspace,
                subject_ref=command.subject_ref,
                reason=command.reason,
                decision_id=command.decision_id,
                changed_files=list(command.changed_files),
                receipt_refs=list(command.receipt_refs),
                metadata=dict(command.metadata),
            )
        except (RuntimeError, ValueError) as exc:
            return RollbackLedgerResultV1(
                ok=False,
                error_code="record_rollback_ledger_failed",
                error_message=str(exc),
            )
        return RollbackLedgerResultV1(ok=True, entry=entry)

    def get_runtime_receipt(self, query: GetRuntimeReceiptQueryV1) -> RuntimeReceiptResultV1:
        receipt = self._runtime.get_runtime_receipt(
            workspace=query.workspace,
            receipt_id=query.receipt_id,
        )
        if receipt is None:
            return RuntimeReceiptResultV1(
                ok=False,
                error_code="runtime_receipt_not_found",
                error_message=f"Runtime receipt not found: {query.receipt_id}",
            )
        return RuntimeReceiptResultV1(ok=True, receipt=receipt)

    def get_handoff_pack(self, query: GetHandoffPackQueryV1) -> HandoffPackResultV1:
        handoff = self._runtime.get_handoff_pack(
            workspace=query.workspace,
            handoff_id=query.handoff_id,
        )
        if handoff is None:
            return HandoffPackResultV1(
                ok=False,
                error_code="handoff_pack_not_found",
                error_message=f"Handoff pack not found: {query.handoff_id}",
            )
        return HandoffPackResultV1(ok=True, handoff=handoff)

    def rehydrate_handoff_pack(
        self,
        command: RehydrateHandoffPackCommandV1,
    ) -> HandoffRehydrationResultV1:
        if self._mode() is CognitiveRuntimeMode.OFF:
            return HandoffRehydrationResultV1(
                ok=False,
                error_code="cognitive_runtime_disabled",
                error_message="Cognitive Runtime is disabled by runtime mode.",
            )
        try:
            rehydration = self._runtime.rehydrate_handoff_pack(
                workspace=command.workspace,
                handoff_id=command.handoff_id,
                target_role=command.target_role,
                target_session_id=command.target_session_id,
            )
        except ValueError as exc:
            return HandoffRehydrationResultV1(
                ok=False,
                error_code="handoff_pack_not_found",
                error_message=str(exc),
            )
        except RuntimeError as exc:
            return HandoffRehydrationResultV1(
                ok=False,
                error_code="rehydrate_handoff_pack_failed",
                error_message=str(exc),
            )
        return HandoffRehydrationResultV1(ok=True, rehydration=rehydration)


def get_cognitive_runtime_public_service() -> CognitiveRuntimePublicService:
    return CognitiveRuntimePublicService(runtime=CognitiveRuntimeService())
