"""Cognitive-runtime methods for `RoleRuntimeService`.

Lossless split: this module holds ``_CognitiveRuntimeMixin`` — the
cognitive-runtime shadow-artifact emission, mainline preflight, and the
request-preparation methods that thread them together. They are factored into a
mixin so the concrete class keeps every method as a real class attribute
(preserving monkeypatch / attribute-identity behavior) while their bodies live
here.

The cross-cell imports for the Cognitive Runtime factory service and the
``CognitiveMiddleware`` stay lazy / in-body exactly as before — both because
they break real import cycles and because the test suite monkeypatches
``CognitiveMiddleware`` on its own module, which a call-time import honors.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.profile.public.service import RoleTurnRequest
from polaris.cells.roles.runtime.public.cognitive_strategy import (
    _apply_forced_transaction_tool_guidance,
    _build_cognitive_strategy_override,
    _copy_cognitive_guidance,
    _enforce_required_context_os,
    _metadata_flag_enabled,
    _resolve_cognitive_runtime_blocker_approval,
)
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleSessionCommandV1,
    ExecuteRoleTaskCommandV1,
    RoleExecutionResultV1,
)
from polaris.cells.roles.runtime.public.result_mapping import (
    _copy_result_metadata,
    _extract_turn_envelope_metadata,
)
from polaris.kernelone.context.runtime_feature_flags import (
    CognitiveRuntimeMode,
    resolve_cognitive_runtime_mode,
)
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


class _CognitiveRuntimeMixin:
    """Cognitive-runtime preflight and shadow-artifact behavior for ``RoleRuntimeService``."""

    if TYPE_CHECKING:
        # Provided by ``RoleRuntimeService`` via the MRO; declared here so the
        # ``self._build_*_request`` calls typecheck without importing the
        # concrete class (which would create an import cycle).
        @staticmethod
        def _build_task_request(command: ExecuteRoleTaskCommandV1) -> RoleTurnRequest: ...

        @staticmethod
        def _build_session_request(
            command: ExecuteRoleSessionCommandV1,
            *,
            include_session_snapshot: bool = False,
        ) -> RoleTurnRequest: ...

    def _emit_cognitive_runtime_shadow_artifacts(
        self,
        *,
        source: str,
        workspace: str,
        role: str,
        task_id: str | None,
        session_id: str | None,
        run_id: str | None,
        result: RoleExecutionResultV1,
        metadata: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        required = _metadata_flag_enabled(context, metadata, key="cognitive_runtime_required")
        mode = resolve_cognitive_runtime_mode(context=context, metadata=metadata)
        evidence: dict[str, Any] = {
            "required": required,
            "source": source,
            "cognitive_runtime_mode": mode.value,
            "receipt_recorded": False,
            "handoff_exported": False,
        }
        if mode is CognitiveRuntimeMode.OFF:
            if required:
                raise RuntimeError("cognitive_runtime_required_but_off")
            return evidence
        try:
            from polaris.cells.factory.cognitive_runtime.public.contracts import (
                ExportHandoffPackCommandV1,
                RecordRuntimeReceiptCommandV1,
            )
            from polaris.cells.factory.cognitive_runtime.public.service import (
                get_cognitive_runtime_public_service,
            )

            service = get_cognitive_runtime_public_service()
            try:
                turn_envelope = _extract_turn_envelope_metadata(result)
                result_metadata = _copy_result_metadata(result.metadata)
                context_os_audit = result_metadata.get("context_os_audit")
                receipt_payload: dict[str, Any] = {
                    "source": source,
                    "role": role,
                    "task_id": task_id,
                    "status": result.status,
                    "ok": result.ok,
                    "tool_calls": list(result.tool_calls),
                    "artifacts": list(result.artifacts),
                    "output_length": len(str(result.output or "")),
                    "has_thinking": bool(str(result.thinking or "").strip()),
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                    "cognitive_runtime_mode": mode.value,
                }
                if isinstance(context_os_audit, Mapping):
                    receipt_payload["context_os_audit"] = dict(context_os_audit)
                    evidence["context_os_audit_recorded"] = True
                receipt_result = service.record_runtime_receipt(
                    RecordRuntimeReceiptCommandV1(
                        workspace=workspace,
                        receipt_type="role_runtime_turn",
                        session_id=session_id,
                        run_id=run_id,
                        payload=receipt_payload,
                        turn_envelope=turn_envelope,
                    )
                )
                if not bool(getattr(receipt_result, "ok", False)):
                    error_message = str(getattr(receipt_result, "error_message", "") or "").strip()
                    error_code = str(getattr(receipt_result, "error_code", "") or "").strip()
                    raise RuntimeError(error_message or error_code or "runtime_receipt_failed")
                receipt = getattr(receipt_result, "receipt", None)
                receipt_id = str(getattr(receipt, "receipt_id", "") or "").strip()
                if required and not receipt_id:
                    raise RuntimeError("runtime_receipt_missing_id")
                if receipt_id:
                    evidence["receipt_id"] = receipt_id
                evidence["receipt_recorded"] = True
                if session_id:
                    handoff_turn_envelope = dict(turn_envelope)
                    if receipt_id:
                        receipt_ids = list(handoff_turn_envelope.get("receipt_ids") or [])
                        if receipt_id not in receipt_ids:
                            receipt_ids.append(receipt_id)
                        handoff_turn_envelope["receipt_ids"] = receipt_ids
                    handoff_result = service.export_handoff_pack(
                        ExportHandoffPackCommandV1(
                            workspace=workspace,
                            session_id=session_id,
                            run_id=run_id,
                            reason=f"{source}:{result.status}",
                            turn_envelope=handoff_turn_envelope,
                        )
                    )
                    if not bool(getattr(handoff_result, "ok", False)):
                        error_message = str(getattr(handoff_result, "error_message", "") or "").strip()
                        error_code = str(getattr(handoff_result, "error_code", "") or "").strip()
                        raise RuntimeError(error_message or error_code or "handoff_export_failed")
                    handoff = getattr(handoff_result, "handoff", None)
                    handoff_id = str(getattr(handoff, "handoff_id", "") or "").strip()
                    if required and not handoff_id:
                        raise RuntimeError("handoff_missing_id")
                    if handoff_id:
                        evidence["handoff_id"] = handoff_id
                    evidence["handoff_exported"] = True
                else:
                    evidence["handoff_skipped_reason"] = "no_session_id"
            finally:
                service.close()
        except (
            AttributeError,
            ImportError,
            LookupError,
            OSError,
            RuntimeError,
            SQLAlchemyError,
            TypeError,
            ValueError,
        ) as exc:
            evidence["error_message"] = str(exc)
            if required:
                raise
            logger.warning(
                "Failed to emit Cognitive Runtime shadow artifacts for role=%s session=%s run=%s",
                role,
                session_id,
                run_id,
                exc_info=True,
            )
        return evidence

    @staticmethod
    async def _apply_cognitive_runtime_preflight(
        *,
        request: RoleTurnRequest,
        role: str,
        workspace: str,
        session_id: str | None,
    ) -> RoleTurnRequest:
        context_override = dict(request.context_override or {})
        metadata = dict(request.metadata or {})
        mode = resolve_cognitive_runtime_mode(context=context_override, metadata=metadata)
        required = _metadata_flag_enabled(
            context_override,
            metadata,
            key="cognitive_runtime_required",
        )
        if mode is CognitiveRuntimeMode.OFF:
            if required:
                raise RuntimeError("cognitive_runtime_required_but_off")
            metadata["cognitive_runtime_preflight"] = {
                "mode": mode.value,
                "applied": False,
                "reason": "off",
            }
            request.metadata = metadata
            return request

        if mode is not CognitiveRuntimeMode.MAINLINE:
            metadata["cognitive_runtime_preflight"] = {
                "mode": mode.value,
                "applied": False,
                "reason": "shadow_mode",
            }
            request.metadata = metadata
            return request

        from polaris.kernelone.cognitive.middleware import CognitiveMiddleware

        middleware = CognitiveMiddleware(workspace=workspace, enabled=True)
        cognitive_context = await middleware.process(
            message=str(request.message or ""),
            role_id=role,
            session_id=session_id,
        )
        if not bool(cognitive_context.get("enabled")):
            # Telemetry refactor: the middleware degrades to enabled=False on an infra
            # failure; carry its degraded_reason into the breadcrumb. Infra degradation is
            # evidence, not a reason to skip the real bound LLM call: governance blockers
            # still fail closed below, but missing optional cognitive files must not stop
            # PM/CE/Director from exercising the production model route.
            degraded_reason = str(cognitive_context.get("degraded_reason") or "").strip()
            if degraded_reason:
                metadata["cognitive_runtime_preflight"] = {
                    "mode": mode.value,
                    "applied": False,
                    "degraded": True,
                    "degraded_reason": degraded_reason,
                    "reason": f"mainline_degraded:{degraded_reason}",
                }
                context_override["cognitive_guidance"] = {
                    "degraded": True,
                    "degraded_reason": degraded_reason,
                    "intent_type": "unknown",
                    "execution_path": "unknown",
                    "verification_needed": True,
                    "blocked_tools": (),
                }
                request.context_override = context_override
                request.metadata = metadata
                return request
            metadata["cognitive_runtime_preflight"] = {
                "mode": mode.value,
                "applied": False,
                "degraded": False,
                "reason": "mainline_unavailable",
            }
            request.metadata = metadata
            raise RuntimeError("cognitive_runtime_mainline_unavailable")

        approved_blocker: dict[str, str] | None = None
        block_reason = ""
        if bool(cognitive_context.get("blocked")):
            reason = str(cognitive_context.get("block_reason") or "blocked").strip()
            approved_blocker = _resolve_cognitive_runtime_blocker_approval(
                context=context_override,
                metadata=metadata,
            )
            if approved_blocker is None:
                raise RuntimeError(f"cognitive_runtime_blocked:{reason}")
            block_reason = reason

        guidance = _copy_cognitive_guidance(cognitive_context)
        forced_transaction_tool_choice_override = _apply_forced_transaction_tool_guidance(
            guidance,
            context_override,
        )
        if approved_blocker is not None:
            guidance["approved_blocker"] = True
            guidance["block_reason"] = block_reason
        context_override["cognitive_guidance"] = guidance
        blocked_tools = tuple(guidance.get("blocked_tools") or ())
        if blocked_tools:
            metadata["cognitive_tool_policy"] = {
                "source": "cognitive_runtime_mainline",
                "blocked_tools": blocked_tools,
            }
        strategy_override = _build_cognitive_strategy_override(guidance)
        if strategy_override:
            metadata["cognitive_strategy_override"] = strategy_override
        metadata["cognitive_runtime_preflight"] = {
            "mode": mode.value,
            "applied": True,
            "blocked": False,
            "intent_type": guidance["intent_type"],
            "execution_path": guidance["execution_path"],
            "verification_needed": guidance["verification_needed"],
            "blocked_tools": blocked_tools,
            "tool_policy_applied": bool(blocked_tools),
            "strategy_override_applied": bool(strategy_override),
            "forced_transaction_tool_choice_override": forced_transaction_tool_choice_override,
        }
        if forced_transaction_tool_choice_override:
            metadata["cognitive_runtime_preflight"].update(
                {
                    "original_intent_type": guidance["original_intent_type"],
                    "original_execution_path": guidance["original_execution_path"],
                    "original_verification_needed": guidance["original_verification_needed"],
                }
            )
        if approved_blocker is not None:
            metadata["cognitive_runtime_preflight"].update(
                {
                    "approved_blocker": True,
                    "original_blocked": True,
                    "block_reason": block_reason,
                    "approval_mode": approved_blocker["mode"],
                    "approval_source": approved_blocker["source"],
                    "approval_scope": approved_blocker["scope"],
                    "approved_by": approved_blocker["approved_by"],
                }
            )
        request.context_override = context_override
        request.metadata = metadata
        return request

    async def _prepare_task_request(self, command: ExecuteRoleTaskCommandV1) -> RoleTurnRequest:
        request = self._build_task_request(command)
        request = _enforce_required_context_os(request)
        return await self._apply_cognitive_runtime_preflight(
            request=request,
            role=command.role,
            workspace=command.workspace,
            session_id=command.session_id,
        )

    async def _prepare_session_request(
        self,
        command: ExecuteRoleSessionCommandV1,
        *,
        include_session_snapshot: bool = False,
    ) -> RoleTurnRequest:
        request = self._build_session_request(
            command,
            include_session_snapshot=include_session_snapshot,
        )
        request = _enforce_required_context_os(request)
        return await self._apply_cognitive_runtime_preflight(
            request=request,
            role=command.role,
            workspace=command.workspace,
            session_id=command.session_id,
        )
