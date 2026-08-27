"""Primary call-path mixins for LLMInvoker (call, cache, fallback ladder)."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from polaris.cells.control_plane.run_ledger.public import (
    project_native_tool_call_envelopes_to_metadata,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FactoryRoleSemanticRequestIdentityV1,
)
from polaris.kernelone.llm.engine import AIExecutor
from polaris.kernelone.llm.engine._executor_base import coerce_required_flag

from ...llm_cache import get_global_llm_cache
from ..context_audit import (
    build_final_request_context_audit_for_request,
)
from ..error_handling import (
    ERROR_CATEGORY_CANCELLED,
    build_native_tool_unavailable_error,
    classify_error,
    is_response_format_unsupported,
    is_retryable_error,
)
from ..factory_dispatch_propagation import (
    FactorySemanticDispatchPropagationPort,
    enforce_factory_aware_final_request_evidence_coverage,
)
from ..final_request_metrics import validated_final_context_evidence
from ..final_request_tool_surface import assert_native_tool_call_in_final_request_surface
from ..helpers import (
    attach_complete_native_tool_argument_audits,
    build_native_tool_call_envelope_payloads,
    extract_native_tool_calls,
)
from ..invoker_phases import FallbackLadderResult, read_response_status
from ..request_preparer import LLMRequestPreparer
from ..response_types import LLMResponse, PreparedLLMRequest
from ..stream_handler import (
    normalize_stream_chunk,  # noqa: F401
)

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
    from polaris.cells.roles.profile.public.service import RoleProfile

from ._helpers import (
    _enforce_factory_semantic_zero_transport,
    _FactorySemanticDispatchNotEnabledError,
    _final_request_context_tokens,
    _get_cognitive_runtime_receipt_deps,
    _invoke_executor_with_factory_dispatch,
    _invoker_owned_factory_semantic_identity,
    _normalize_provider_usage,
    _prepared_request_temperature,
    _profile_lacks_forced_tool_choice,
    _recover_text_tool_calls_from_response_text,
    _reprepare_semantic_retry,
    _required_tool_not_called_error,
    _RoleBindingFallbackFailure,
    _store_active_request_context_snapshot,
    _store_call_start_context_snapshot,
    _with_context_os_audit,
    _with_context_snapshot_diagnostics,
    _with_final_request_context_audit,
    _with_optional_final_request_context_audit,
)

logger = logging.getLogger(__name__)


class _InvokerCallMixin:
    """Synchronous-style LLM call path, cache, receipts, and error projection."""

    __slots__ = ()

    if TYPE_CHECKING:
        workspace: str
        _enable_cache: bool
        _event_emitter: Any
        _executor: Any
        _formatter: Any
        _model_catalog: Any

        def _profile_for_healthy_binding(self, role_id: str, profile: Any) -> Any: ...
        def _try_retryable_exception_role_binding_fallback(self, *args: Any, **kwargs: Any) -> Any: ...
        def _try_role_binding_fallback(self, *args: Any, **kwargs: Any) -> Any: ...

    async def _emit_required_tool_retry_request_audit(
        self,
        *,
        prepared: PreparedLLMRequest,
        request: Any,
        request_profile: RoleProfile,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        model: str,
        call_id: str,
        event_emitter: Any | None,
        retry_decision: str,
    ) -> None:
        await _store_active_request_context_snapshot(
            workspace=self.workspace,
            active_request=request,
            prepared=prepared,
            profile=request_profile,
            run_id=run_id,
            call_id=f"{call_id}-{retry_decision}",
        )
        audit_metadata = _with_final_request_context_audit(
            {
                "fallback_request": True,
                "retry_decision": retry_decision,
                "context_snapshot_ref": self._extract_context_snapshot_ref(request),
            },
            prepared=prepared,
            active_request=request,
            profile=request_profile,
        )
        self._emit_call_retry_event(
            event_emitter=event_emitter,
            role=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=str(getattr(request_profile, "model", "") or model),
            provider=str(getattr(request_profile, "provider_id", "") or ""),
            call_id=call_id,
            retry_decision=retry_decision,
            backoff_seconds=0.0,
            metadata=audit_metadata,
        )

    async def _retry_required_tool_if_missing(
        self,
        *,
        request_preparer: LLMRequestPreparer,
        executor: Any,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        response: Any,
        active_request: Any,
        response_error: str,
        is_response_ok: bool,
        native_tool_fallback: bool,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        model: str,
        call_id: str,
        event_emitter: Any | None,
    ) -> tuple[PreparedLLMRequest, Any, Any, bool, str, bool]:
        """Run the required-tool retry rung for the currently selected binding."""

        if is_response_ok:
            response_error = _required_tool_not_called_error(
                prepared=prepared,
                active_request=active_request,
                response=response,
                profile=profile,
            )
            if response_error:
                is_response_ok = False

        if is_response_ok or "required_tool_not_called" not in str(response_error or "").lower():
            return prepared, active_request, response, is_response_ok, response_error, native_tool_fallback

        validation_prepared = prepared
        validation_active_request = active_request
        if _profile_lacks_forced_tool_choice(profile):
            retry_request = request_preparer._build_required_tool_text_fallback_request(
                prepared=prepared,
                profile=profile,
                error_message=response_error,
            )
            prepared, active_request = await _reprepare_semantic_retry(
                request_preparer=request_preparer,
                prepared=prepared,
                request=retry_request,
                profile=profile,
            )
            await self._emit_required_tool_retry_request_audit(
                prepared=prepared,
                request=active_request,
                request_profile=profile,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                call_id=call_id,
                event_emitter=event_emitter,
                retry_decision="required_tool_text_fallback",
            )
            response = await _invoke_executor_with_factory_dispatch(
                executor=executor,
                prepared=prepared,
                request=active_request,
                profile=profile,
            )
            native_tool_fallback = True
            logger.warning("[invoker] required-tool text fallback: provider cannot force native tool_choice")
            is_response_ok, response_error = read_response_status(response)
            if is_response_ok:
                response_error = _required_tool_not_called_error(
                    prepared=validation_prepared,
                    active_request=validation_active_request,
                    response=response,
                    profile=profile,
                )
                if response_error:
                    response_error = f"required_tool_text_fallback_not_dispatched: {response_error}"
                    is_response_ok = False
        else:
            retry_request = request_preparer._build_required_tool_retry_request(
                prepared=prepared,
                profile=profile,
                error_message=response_error,
            )
            prepared, active_request = await _reprepare_semantic_retry(
                request_preparer=request_preparer,
                prepared=prepared,
                request=retry_request,
                profile=profile,
            )
            await self._emit_required_tool_retry_request_audit(
                prepared=prepared,
                request=active_request,
                request_profile=profile,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                call_id=call_id,
                event_emitter=event_emitter,
                retry_decision="required_tool_not_called_retry",
            )
            response = await _invoke_executor_with_factory_dispatch(
                executor=executor,
                prepared=prepared,
                request=active_request,
                profile=profile,
            )
            logger.warning("[invoker] required-tool re-ask: provider returned prose without required tool call")
            is_response_ok, response_error = read_response_status(response)
            if is_response_ok:
                response_error = _required_tool_not_called_error(
                    prepared=prepared,
                    active_request=active_request,
                    response=response,
                    profile=profile,
                )
                if response_error:
                    is_response_ok = False

        return prepared, active_request, response, is_response_ok, response_error, native_tool_fallback

    def _get_executor(self) -> Any:
        """Get or create AIExecutor instance (lazy, respects DI injection)."""
        if self._executor is not None:
            return self._executor
        return AIExecutor(
            workspace=self.workspace,
            final_request_receipt_sink=self._record_final_request_receipt,
        )

    def _record_final_request_receipt(self, receipt: Any) -> None:
        if not isinstance(receipt, dict):
            return
        raw_payload = receipt.get("payload")
        payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
        receipt_required = coerce_required_flag(payload.get("cognitive_runtime_required")) or coerce_required_flag(
            payload.get("context_os_expected")
        )
        receipt_type = str(receipt.get("receipt_type") or "contextos.final_request").strip()
        if not receipt_type:
            receipt_type = "contextos.final_request"

        trace_refs = self._normalize_trace_refs(receipt.get("trace_refs"), payload.get("trace_id"))
        try:
            command_cls, get_service = _get_cognitive_runtime_receipt_deps()
            result = get_service().record_runtime_receipt(
                command_cls(
                    workspace=self.workspace or ".",
                    receipt_type=receipt_type,
                    payload=payload,
                    session_id=self._optional_str(payload.get("session_id")),
                    run_id=self._optional_str(payload.get("run_id")),
                    trace_refs=trace_refs,
                    turn_envelope={
                        "source": "roles.kernel.llm_invoker",
                        "receipt_type": receipt_type,
                        "trace_id": self._optional_str(payload.get("trace_id")),
                        "task_id": self._optional_str(payload.get("task_id")),
                        "role": self._optional_str(payload.get("role")),
                    },
                )
            )
        except Exception as exc:
            logger.warning("[LLMInvoker] contextos final request receipt failed: %s", exc)
            if receipt_required:
                raise RuntimeError("contextos final request receipt failed in required mode") from exc
            return
        if not bool(getattr(result, "ok", False)):
            message = str(getattr(result, "error_message", "") or getattr(result, "error_code", "") or "").strip()
            logger.warning("[LLMInvoker] contextos final request receipt rejected: %s", message)
            if receipt_required:
                raise RuntimeError(message or "contextos final request receipt rejected in required mode")

    @staticmethod
    def _normalize_trace_refs(raw_refs: Any, fallback_trace_id: Any = None) -> tuple[str, ...]:
        refs: list[str] = []
        if isinstance(raw_refs, (list, tuple, set, frozenset)):
            refs.extend(str(item).strip() for item in raw_refs if str(item or "").strip())
        else:
            text = str(raw_refs or "").strip()
            if text:
                refs.append(text)
        fallback = str(fallback_trace_id or "").strip()
        if fallback and fallback not in refs:
            refs.append(fallback)
        return tuple(refs)

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _extract_context_snapshot_ref(request: Any) -> str | None:
        """Extract context_snapshot_ref from an AIRequest's context dict, if present."""
        ctx = getattr(request, "context", None)
        if isinstance(ctx, dict):
            ref = ctx.get("context_snapshot_ref")
            if isinstance(ref, str) and ref.strip():
                return ref.strip()
        return None

    @staticmethod
    def _extract_final_context_snapshot_ref(
        prepared: PreparedLLMRequest,
        request: Any,
    ) -> str | None:
        port = prepared.factory_dispatch_port
        evidence = validated_final_context_evidence(
            port,
            expected_port_type=FactorySemanticDispatchPropagationPort,
        )
        if evidence is not None:
            return evidence[0]
        return _InvokerCallMixin._extract_context_snapshot_ref(request)

    @staticmethod
    def _profile_bound_request_for_evidence(request: Any, profile: RoleProfile) -> Any:
        """Return an evidence-only request view pinned to ``profile`` binding."""

        try:
            bound_request = copy.copy(request)
        except (TypeError, ValueError):
            bound_request = request

        ctx = getattr(request, "context", None)
        if isinstance(ctx, dict):
            with contextlib.suppress(AttributeError, TypeError):
                bound_request.context = dict(ctx)

        provider_id = str(getattr(profile, "provider_id", "") or "").strip()
        model = str(getattr(profile, "model", "") or "").strip()
        if provider_id:
            with contextlib.suppress(AttributeError, TypeError):
                bound_request.provider_id = provider_id
        if model:
            with contextlib.suppress(AttributeError, TypeError):
                bound_request.model = model
        return bound_request

    def _build_call_error_response(
        self,
        *,
        prepared: PreparedLLMRequest,
        active_request: Any,
        response_error: str,
        profile: RoleProfile,
        native_tool_fallback: bool,
        native_response_fallback: bool,
        allow_native_tool_text_fallback: bool,
        model: str,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> LLMResponse:
        """Emit the call_error event and build the failure ``LLMResponse``."""
        active_request = self._profile_bound_request_for_evidence(active_request, profile)
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        classified = classify_error(response_error)
        active_context = active_request.context if isinstance(active_request.context, dict) else {}
        provider_id = str(getattr(active_request, "provider_id", None) or getattr(profile, "provider_id", "") or "")
        active_model = str(getattr(active_request, "model", None) or getattr(profile, "model", "") or model or "")
        event_metadata = _with_final_request_context_audit(
            {
                "provider": provider_id,
                "provider_id": provider_id,
                "model": active_model,
                "native_tool_calling_fallback": native_tool_fallback,
                "native_response_format_fallback": native_response_fallback,
                "native_tool_mode": str(active_context.get("native_tool_mode") or prepared.native_tool_mode),
                "response_format_mode": str(
                    active_context.get("response_format_mode") or prepared.response_format_mode
                ),
                "native_tool_text_fallback_allowed": allow_native_tool_text_fallback,
                "context_snapshot_ref": self._extract_final_context_snapshot_ref(prepared, active_request),
            },
            prepared=prepared,
            active_request=active_request,
            profile=profile,
        )
        self._emit_call_error_event(
            event_emitter=event_emitter,
            role=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=model,
            error_category=classified,
            error_message=response_error or "LLM call failed",
            call_id=call_id,
            elapsed_ms=elapsed_ms,
            metadata=_with_context_os_audit(event_metadata, prepared),
        )
        return LLMResponse(
            content="",
            error=response_error or "LLM call failed",
            error_category=classified,
            metadata=_with_context_os_audit(
                _with_final_request_context_audit(
                    {
                        "model": model,
                        "provider": provider_id,
                        "provider_id": provider_id,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "native_tool_calling_fallback": native_tool_fallback,
                        "native_response_format_fallback": native_response_fallback,
                        "native_tool_text_fallback_allowed": allow_native_tool_text_fallback,
                        "run_id": run_id,
                        "workspace": self.workspace,
                        "attempt": attempt,
                        "context_snapshot_ref": self._extract_final_context_snapshot_ref(prepared, active_request),
                    },
                    prepared=prepared,
                    active_request=active_request,
                    profile=profile,
                ),
                prepared,
            ),
        )

    def _finalize_call_response(
        self,
        *,
        cache: Any,
        prepared: PreparedLLMRequest,
        active_request: Any,
        response: Any,
        cache_eligible: bool,
        prompt_fingerprint: str | None,
        temperature: float,
        model: str,
        profile: RoleProfile,
        prompt_tokens: int,
        turn_round: int,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> LLMResponse:
        """Normalize a successful response, store cache, emit end, and build result."""
        raw_payload = response.raw if isinstance(response.raw, dict) else {}
        # DEBUG: Log raw LLM response for debugging parsing issues
        output_text = str(getattr(response, "output", "") or "")
        logger.debug(
            "[LLMInvoker] Raw LLM response received: model=%s provider=%s output_length=%d output_preview=%r",
            raw_payload.get("model", "unknown"),
            raw_payload.get("provider", "unknown"),
            len(output_text),
            output_text[:500] if output_text else "<empty>",
        )
        response_text = output_text
        if not response_text.strip() and raw_payload:
            try:
                from polaris.kernelone.llm.engine import ResponseNormalizer

                response_text = ResponseNormalizer.extract_text(raw_payload)
            except (RuntimeError, ValueError):
                response_text = str(getattr(response, "output", "") or "")

        response_model_name = str((getattr(response, "model", None) or raw_payload.get("model") or model) or "")
        response_provider = str(
            (
                getattr(response, "provider_id", None)
                or raw_payload.get("provider_id")
                or raw_payload.get("provider")
                or ""
            )
            or ""
        )
        native_tool_calls, native_tool_provider = extract_native_tool_calls(
            raw_payload, provider_id=response_provider, model=response_model_name, response_text=response_text
        )
        active_context = getattr(active_request, "context", None)
        active_context_payload = active_context if isinstance(active_context, dict) else {}
        text_fallback_requested = bool(active_context_payload.get("required_tool_text_fallback"))
        text_tool_recovery_metadata: dict[str, Any] = {
            "compatibility_mode": "required_tool_text_fallback" if text_fallback_requested else "native_tools",
            "text_fallback_requested": text_fallback_requested,
            "native_tool_surface_absent_because_text_fallback": text_fallback_requested,
            "text_tool_parser_attempted": False,
            "text_tool_decoded_calls_count": 0,
        }
        if not native_tool_calls:
            recovery = _recover_text_tool_calls_from_response_text(
                response_text=response_text,
                raw_payload=raw_payload,
                prepared=prepared,
                provider_hint=native_tool_provider,
            )
            text_tool_recovery_metadata.update(
                {
                    "text_tool_parser_attempted": recovery.parser_attempted,
                    "text_tool_parser_available": recovery.parser_available,
                    "text_tool_parser_error": recovery.error,
                    "text_tool_decoded_calls_count": len(recovery.calls),
                }
            )
            if recovery.calls:
                native_tool_calls = list(recovery.calls)
                native_tool_provider = "openai"
                text_tool_recovery_metadata.update(
                    {
                        "text_tool_recovery_used": True,
                        "text_tool_recovery_call_count": len(recovery.calls),
                        "text_tool_recovery_provider": "toolkit_parser",
                    }
                )
            elif text_fallback_requested:
                text_tool_recovery_metadata["failure_class"] = "required_tool_text_fallback_not_dispatched"
        for native_tool_call in native_tool_calls:
            assert_native_tool_call_in_final_request_surface(
                native_tool_call=native_tool_call,
                active_request=active_request,
                prepared=prepared,
            )
        native_tool_calls = attach_complete_native_tool_argument_audits(
            native_tool_calls,
            provider=native_tool_provider,
        )
        native_tool_call_envelopes = build_native_tool_call_envelope_payloads(
            native_tool_calls,
            provider=native_tool_provider,
        )
        native_tool_metadata: dict[str, Any] = {}
        project_native_tool_call_envelopes_to_metadata(native_tool_metadata, native_tool_call_envelopes)
        argument_audit_missing_call_ids = [
            str(envelope.get("call_id") or "").strip()
            for envelope in native_tool_call_envelopes
            if not isinstance((envelope.get("metadata") or {}).get("provider_argument_audit"), dict)
        ]
        native_tool_metadata["tool_call_argument_audit_projection"] = {
            "schema_version": "llm.tool_call_argument_audit_projection.v1",
            "source": "complete_provider_response",
            "tool_call_count": len(native_tool_call_envelopes),
            "audit_present_count": len(native_tool_call_envelopes) - len(argument_audit_missing_call_ids),
            "audit_missing_count": len(argument_audit_missing_call_ids),
            "missing_call_ids": argument_audit_missing_call_ids,
        }
        native_tool_metadata.update(text_tool_recovery_metadata)

        elapsed_ms = (time.perf_counter() - start_time) * 1000
        provider_usage = _normalize_provider_usage(getattr(response, "usage", None)) or _normalize_provider_usage(
            raw_payload.get("usage")
        )
        estimated_completion_tokens = len(response_text) // 2
        event_prompt_tokens = int(provider_usage["prompt_tokens"]) if provider_usage else prompt_tokens
        completion_tokens = (
            int(provider_usage["completion_tokens"])
            if provider_usage and int(provider_usage["completion_tokens"]) > 0
            else estimated_completion_tokens
        )
        total_token_estimate = (
            int(provider_usage["total_tokens"])
            if provider_usage and int(provider_usage["total_tokens"]) > 0
            else (
                prepared.context_result.token_estimate + completion_tokens
                if prepared.context_result
                else completion_tokens
            )
        )

        self._store_cache(
            cache=cache,
            prepared=prepared,
            cache_eligible=cache_eligible,
            prompt_fingerprint=prompt_fingerprint,
            temperature=temperature,
            model=model,
            response_text=response_text,
            completion_tokens=completion_tokens,
        )

        event_metadata: dict[str, Any] = {
            "elapsed_ms": round(elapsed_ms, 2),
            "cached": False,
            "source": "llm",
            "compression_applied": prepared.context_result.compression_applied if prepared.context_result else False,
            "turn_round": turn_round,
            "context_snapshot_ref": self._extract_final_context_snapshot_ref(prepared, active_request),
            **native_tool_metadata,
        }
        event_metadata = _with_context_snapshot_diagnostics(event_metadata, active_request)
        if provider_usage is not None:
            event_metadata["usage"] = provider_usage
            event_metadata["usage_source"] = "provider"
        event_metadata = _with_final_request_context_audit(
            event_metadata,
            prepared=prepared,
            active_request=active_request,
            profile=profile,
        )
        final_context_tokens = _final_request_context_tokens(
            event_metadata,
            prepared.context_result.token_estimate if prepared.context_result else None,
        )

        self._emit_call_end_event(
            event_emitter=event_emitter,
            role=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=response_model_name,
            provider=response_provider,
            prompt_tokens=event_prompt_tokens,
            completion_tokens=completion_tokens,
            call_id=call_id,
            context_tokens_after=final_context_tokens,
            compression_strategy=prepared.context_result.compression_strategy if prepared.context_result else None,
            response_content=response_text,
            tool_calls_count=len(native_tool_calls),
            metadata=_with_context_os_audit(event_metadata, prepared),
        )

        response_metadata: dict[str, Any] = {
            "model": response_model_name,
            "provider": response_provider,
            **native_tool_metadata,
            "elapsed_ms": round(elapsed_ms, 2),
            "run_id": run_id,
            "workspace": self.workspace,
            "attempt": attempt,
            "turn_round": turn_round,
            # SSOT Fix: Pass context token count for context panel display
            "context_tokens": int(final_context_tokens or 0),
            "context_snapshot_ref": self._extract_final_context_snapshot_ref(prepared, active_request),
        }
        response_metadata = _with_context_snapshot_diagnostics(response_metadata, active_request)
        if provider_usage is not None:
            response_metadata["usage"] = provider_usage
            response_metadata["usage_source"] = "provider"
        response_metadata = _with_final_request_context_audit(
            response_metadata,
            prepared=prepared,
            active_request=active_request,
            profile=profile,
        )

        return LLMResponse(
            content=response_text,
            token_estimate=total_token_estimate,
            tool_calls=native_tool_calls,
            tool_call_provider=native_tool_provider,
            metadata=_with_context_os_audit(
                response_metadata,
                prepared,
            ),
            native_tool_calls=native_tool_calls,
        )

    async def _run_fallback_ladder(
        self,
        *,
        request_preparer: LLMRequestPreparer,
        executor: Any,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        context: ContextRequest,
        response: Any,
        active_request: Any,
        response_model: type | None,
        response_error: str,
        is_response_ok: bool,
        allow_native_tool_text_fallback: bool,
        native_tool_fallback: bool,
        native_response_fallback: bool,
        system_prompt: str,
        temperature: float,
        effective_max_tokens: int,
        platform_retry_max: int,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        model: str,
        call_id: str,
        event_emitter: Any | None,
        factory_semantic_identity: FactoryRoleSemanticRequestIdentityV1 | None,
    ) -> FallbackLadderResult:
        """Run the call-phase fallback ladder after the primary invoke.

        Three rungs in fixed order: response_format fallback,
        reasoning-truncation re-ask, and role-binding fallback. Returns a
        :class:`FallbackLadderResult` carrying the mutated state so the
        orchestrator can repoint its locals.
        """

        async def emit_fallback_request_audit(retry_decision: str, request: Any, request_profile: RoleProfile) -> None:
            await _store_active_request_context_snapshot(
                workspace=self.workspace,
                active_request=request,
                prepared=prepared,
                profile=request_profile,
                run_id=run_id,
                call_id=f"{call_id}-{retry_decision}",
            )
            audit_metadata = _with_final_request_context_audit(
                {
                    "fallback_request": True,
                    "retry_decision": retry_decision,
                    "context_snapshot_ref": self._extract_context_snapshot_ref(request),
                },
                prepared=prepared,
                active_request=request,
                profile=request_profile,
            )
            self._emit_call_retry_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                provider=str(getattr(request_profile, "provider_id", "") or ""),
                call_id=call_id,
                retry_decision=retry_decision,
                backoff_seconds=0.0,
                metadata=audit_metadata,
            )

        _ = allow_native_tool_text_fallback
        if not is_response_ok and prepared.native_response_format and is_response_format_unsupported(response_error):
            active_request = request_preparer._build_structured_fallback_request(
                prepared=prepared, profile=profile, response_model=response_model or dict, mode="chat"
            )
            prepared, active_request = await _reprepare_semantic_retry(
                request_preparer=request_preparer,
                prepared=prepared,
                request=active_request,
                profile=profile,
            )
            await emit_fallback_request_audit("response_format_text_fallback", active_request, profile)
            response = await _invoke_executor_with_factory_dispatch(
                executor=executor,
                prepared=prepared,
                request=active_request,
                profile=profile,
            )
            native_response_fallback = True
            is_response_ok, response_error = read_response_status(response)

        # 5th floor: a reasoning model truncated mid-thought (finish_reason=length)
        # and emitted no visible output / tool call. Re-ask ONCE with a reserved
        # output budget + a "minimal reasoning, emit the tool call now" directive so
        # the write actually lands instead of dying as director_no_materialized_changes.
        response_error_lower = response_error.lower()
        _is_reasoning_truncation = "empty visible output" in response_error_lower and (
            "reasoning truncated" in response_error_lower
            or "reasoning exhausted" in response_error_lower
            or "finish_reason=length" in response_error_lower
        )
        if not is_response_ok and _is_reasoning_truncation:
            active_request = request_preparer._build_reasoning_truncation_retry_request(
                prepared=prepared, profile=profile
            )
            prepared, active_request = await _reprepare_semantic_retry(
                request_preparer=request_preparer,
                prepared=prepared,
                request=active_request,
                profile=profile,
            )
            await emit_fallback_request_audit("reasoning_truncation_retry", active_request, profile)
            response = await _invoke_executor_with_factory_dispatch(
                executor=executor,
                prepared=prepared,
                request=active_request,
                profile=profile,
            )
            logger.warning(
                "[invoker] reasoning-truncation re-ask: reserved output budget + minimal-reasoning directive"
            )
            is_response_ok, response_error = read_response_status(response)

        (
            prepared,
            active_request,
            response,
            is_response_ok,
            response_error,
            native_tool_fallback,
        ) = await self._retry_required_tool_if_missing(
            request_preparer=request_preparer,
            executor=executor,
            prepared=prepared,
            profile=profile,
            response=response,
            active_request=active_request,
            response_error=response_error,
            is_response_ok=is_response_ok,
            native_tool_fallback=native_tool_fallback,
            role_id=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=model,
            call_id=call_id,
            event_emitter=event_emitter,
        )

        if not is_response_ok:
            classified = classify_error(response_error)
            if is_retryable_error(classified):
                fallback_failures: list[_RoleBindingFallbackFailure] = []
                fallback = await self._try_role_binding_fallback(
                    request_preparer=request_preparer,
                    profile=profile,
                    system_prompt=system_prompt,
                    context=context,
                    temperature=temperature,
                    max_tokens=effective_max_tokens,
                    response_model=response_model,
                    platform_retry_max=platform_retry_max,
                    executor=executor,
                    role_id=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    model=model,
                    call_id=call_id,
                    event_emitter=event_emitter,
                    original_error=response_error,
                    failure_sink=fallback_failures,
                    factory_semantic_identity=factory_semantic_identity,
                )
                if fallback is not None:
                    profile, prepared, active_request, response = fallback
                    model = str(getattr(profile, "model", "") or model)
                    native_tool_fallback = False
                    native_response_fallback = False
                    is_response_ok, response_error = read_response_status(response)
                    (
                        prepared,
                        active_request,
                        response,
                        is_response_ok,
                        response_error,
                        native_tool_fallback,
                    ) = await self._retry_required_tool_if_missing(
                        request_preparer=request_preparer,
                        executor=executor,
                        prepared=prepared,
                        profile=profile,
                        response=response,
                        active_request=active_request,
                        response_error=response_error,
                        is_response_ok=is_response_ok,
                        native_tool_fallback=native_tool_fallback,
                        role_id=role_id,
                        run_id=run_id,
                        task_id=task_id,
                        attempt=attempt,
                        model=model,
                        call_id=call_id,
                        event_emitter=event_emitter,
                    )
                elif fallback_failures:
                    latest_failure = fallback_failures[-1]
                    profile = latest_failure.profile
                    prepared = latest_failure.prepared
                    active_request = latest_failure.active_request
                    response_error = latest_failure.error
                    model = latest_failure.model
                    native_tool_fallback = False
                    native_response_fallback = False
                    is_response_ok = False

        return FallbackLadderResult(
            response=response,
            active_request=active_request,
            profile=profile,
            prepared=prepared,
            model=model,
            native_tool_fallback=native_tool_fallback,
            native_response_fallback=native_response_fallback,
            is_response_ok=is_response_ok,
            response_error=response_error,
        )

    def _try_cache_hit(
        self,
        *,
        cache: Any,
        prepared: PreparedLLMRequest,
        context_result: Any,
        cache_eligible: bool,
        prompt_fingerprint: str | None,
        temperature: float,
        model: str,
        profile: RoleProfile,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        turn_round: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> LLMResponse | None:
        """Return a cached ``LLMResponse`` on cache hit, else ``None`` to continue.

        Falls through (returns ``None``) when caching is disabled, no
        fingerprint is supplied, the turn is not cache-eligible, or the cache
        lookup misses.
        """
        if prepared.factory_dispatch_port is not None:
            return None
        if not (self._enable_cache and prompt_fingerprint and cache_eligible):
            return None
        cached = cache.get(
            prompt_fingerprint=prompt_fingerprint,
            context_summary=prepared.context_summary,
            temperature=temperature,
            model=model,
        )
        if not cached:
            return None
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.debug(
            "[LLMInvoker] Cache hit, returning cached response: model=%s length=%d",
            model,
            len(cached),
        )
        cache_metadata = _with_final_request_context_audit(
            {
                "elapsed_ms": round(elapsed_ms, 2),
                "cached": True,
                "source": "cache",
                "compression_applied": context_result.compression_applied if context_result else False,
                "turn_round": turn_round,
                "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
            },
            prepared=prepared,
            active_request=prepared.ai_request,
            profile=profile,
        )
        final_context_tokens = _final_request_context_tokens(
            cache_metadata,
            context_result.token_estimate if context_result else None,
        )
        self._emit_call_end_event(
            event_emitter=event_emitter,
            role=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=model,
            call_id=call_id,
            completion_tokens=len(cached) // 2,
            context_tokens_after=final_context_tokens,
            compression_strategy=context_result.compression_strategy if context_result else None,
            response_content=cached,
            metadata=_with_context_os_audit(cache_metadata, prepared),
        )
        return LLMResponse(
            content=cached,
            token_estimate=len(cached) // 2,
            metadata=_with_context_os_audit(
                {
                    "cached": True,
                    "model": model,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "run_id": run_id,
                    "workspace": self.workspace,
                    "attempt": attempt,
                    "turn_round": turn_round,
                    "native_tool_mode": prepared.native_tool_mode,
                    "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
                },
                prepared,
            ),
        )

    def _store_cache(
        self,
        *,
        cache: Any,
        prepared: PreparedLLMRequest,
        cache_eligible: bool,
        prompt_fingerprint: str | None,
        temperature: float,
        model: str,
        response_text: str,
        completion_tokens: int,
    ) -> None:
        """Persist a successful response to the LLM cache when eligible.

        Re-checks the SAME eligibility flags used by :meth:`_try_cache_hit` so
        get/put stay consistent.
        """
        if self._enable_cache and prompt_fingerprint and cache_eligible:
            cache.put(
                prompt_fingerprint=prompt_fingerprint,
                context_summary=prepared.context_summary,
                temperature=temperature,
                model=model,
                response_content=response_text,
                token_estimate=completion_tokens,
            )

    async def _handle_native_tools_unavailable(
        self,
        *,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        context: ContextRequest,
        model: str,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> LLMResponse:
        """Handle ``native_tool_mode == 'native_tools_unavailable'`` (call phase).

        Emits a call_error event and returns the ``native_tool_unavailable``
        error response. Text-mode tool fallback is disabled; provider-native
        tool schemas are required.
        """
        _ = context
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        tool_error = build_native_tool_unavailable_error(profile)
        error_metadata = _with_final_request_context_audit(
            {
                "native_tool_mode": prepared.native_tool_mode,
                "response_format_mode": prepared.response_format_mode,
                "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
            },
            prepared=prepared,
            active_request=prepared.ai_request,
            profile=profile,
        )
        self._emit_call_error_event(
            event_emitter=event_emitter,
            role=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=model,
            error_category="provider",
            error_message=tool_error,
            call_id=call_id,
            elapsed_ms=elapsed_ms,
            metadata=_with_context_os_audit(error_metadata, prepared),
        )
        response_metadata = _with_final_request_context_audit(
            {
                "model": model,
                "native_tool_mode": prepared.native_tool_mode,
                "response_format_mode": prepared.response_format_mode,
                "run_id": run_id,
                "workspace": self.workspace,
                "attempt": attempt,
                "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
            },
            prepared=prepared,
            active_request=prepared.ai_request,
            profile=profile,
        )
        return LLMResponse(
            content="",
            error=tool_error,
            error_category="provider",
            metadata=_with_context_os_audit(response_metadata, prepared),
        )

    async def call(
        self,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        response_model: type | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        prompt_fingerprint: str | None = None,
        platform_retry_max: int = 1,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        turn_round: int = 0,
        event_emitter: Any | None = None,
    ) -> LLMResponse:
        """Invoke LLM with non-streaming mode."""
        logger.warning(
            "[_InvokerCallMixin.call] ENTRY: profile=%s run_id=%s", getattr(profile, "role_id", "unknown"), run_id
        )
        call_id = uuid.uuid4().hex
        factory_semantic_identity = _invoker_owned_factory_semantic_identity(
            run_id=run_id,
            turn_round=turn_round,
            call_id=call_id,
        )
        run_id = run_id or f"llm_{call_id}"
        task_id = task_id or getattr(context, "task_id", None)
        role_id = str(getattr(profile, "role_id", "unknown") or "unknown")
        model = profile.model or "default"
        from ..helpers import resolve_max_tokens

        context_override = getattr(context, "context_override", None)
        effective_max_tokens = resolve_max_tokens(max_tokens, context_override)
        effective_temperature = temperature

        start_time = time.perf_counter()
        prepared: PreparedLLMRequest | None = None
        active_request: Any | None = None
        request_preparer: LLMRequestPreparer | None = None
        executor: Any | None = None
        prompt_tokens = max(1, len(system_prompt) // 4)

        try:
            profile = self._profile_for_healthy_binding(role_id, profile)
            model = str(getattr(profile, "model", "") or model)
            request_preparer = LLMRequestPreparer(
                workspace=self.workspace,
                formatter=self._formatter,
                model_catalog=self._model_catalog,
            )

            prepared = await request_preparer._prepare_llm_request(
                profile=profile,
                system_prompt=system_prompt,
                context=context,
                temperature=effective_temperature,
                max_tokens=effective_max_tokens,
                stream=False,
                response_model=response_model,
                platform_retry_max=platform_retry_max,
                factory_semantic_identity=factory_semantic_identity,
            )
            _enforce_factory_semantic_zero_transport(prepared)
            effective_temperature = _prepared_request_temperature(prepared, effective_temperature)
            active_request = prepared.ai_request
            await _store_call_start_context_snapshot(
                workspace=self.workspace,
                prepared=prepared,
                profile=profile,
                run_id=run_id,
                call_id=call_id,
            )
            context_result = prepared.context_result
            prompt_tokens = context_result.token_estimate if context_result else len(system_prompt) // 4
            final_context_audit = build_final_request_context_audit_for_request(
                ai_request=prepared.ai_request,
                prepared=prepared,
                profile=profile,
            )
            raw_final_context_tokens = final_context_audit.get("final_request_token_estimate")
            final_context_tokens = int(
                raw_final_context_tokens if raw_final_context_tokens is not None else prompt_tokens
            )
            enforce_factory_aware_final_request_evidence_coverage(
                port=prepared.factory_dispatch_port,
                ai_request=prepared.ai_request,
                audit=final_context_audit,
            )

            self._emit_call_start_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                provider=str(getattr(profile, "provider_id", "") or ""),
                prompt_tokens=prompt_tokens,
                call_id=call_id,
                context_tokens_before=final_context_tokens,
                compression_strategy=context_result.compression_strategy if context_result else None,
                messages=prepared.messages,
                metadata=_with_context_os_audit(
                    _with_context_snapshot_diagnostics(
                        {
                            "temperature": effective_temperature,
                            "max_tokens": effective_max_tokens,
                            "prompt_fingerprint": prompt_fingerprint,
                            "native_tool_mode": prepared.native_tool_mode,
                            "response_format_mode": prepared.response_format_mode,
                            "compression_applied": context_result.compression_applied if context_result else False,
                            "turn_round": turn_round,
                            "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
                            "context_tokens_after": final_context_tokens,
                            "contextTokens": final_context_tokens,
                            "final_request_context_audit": final_context_audit,
                        },
                        prepared.ai_request,
                    ),
                    prepared,
                ),
            )

            cache_eligible = self._is_cache_eligible(prepared=prepared, response_model=response_model)

            if prepared.native_tool_mode == "native_tools_unavailable":
                return await self._handle_native_tools_unavailable(
                    prepared=prepared,
                    profile=profile,
                    context=context,
                    model=model,
                    role_id=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    call_id=call_id,
                    event_emitter=event_emitter,
                    start_time=start_time,
                )

            cache = get_global_llm_cache()
            cache_hit = self._try_cache_hit(
                cache=cache,
                prepared=prepared,
                context_result=context_result,
                cache_eligible=cache_eligible,
                prompt_fingerprint=prompt_fingerprint,
                temperature=effective_temperature,
                model=model,
                profile=profile,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                turn_round=turn_round,
                call_id=call_id,
                event_emitter=event_emitter,
                start_time=start_time,
            )
            if cache_hit is not None:
                return cache_hit

            executor = self._get_executor()
            native_tool_fallback = False
            allow_native_tool_text_fallback = False
            response = await _invoke_executor_with_factory_dispatch(
                executor=executor,
                prepared=prepared,
                request=active_request,
                profile=profile,
            )
            native_response_fallback = False

            is_response_ok, response_error = read_response_status(response)
            if is_response_ok:
                response_error = _required_tool_not_called_error(
                    prepared=prepared,
                    active_request=active_request,
                    response=response,
                    profile=profile,
                )
                if response_error:
                    is_response_ok = False

            ladder = await self._run_fallback_ladder(
                request_preparer=request_preparer,
                executor=executor,
                prepared=prepared,
                profile=profile,
                context=context,
                response=response,
                active_request=active_request,
                response_model=response_model,
                response_error=response_error,
                is_response_ok=is_response_ok,
                allow_native_tool_text_fallback=allow_native_tool_text_fallback,
                native_tool_fallback=native_tool_fallback,
                native_response_fallback=native_response_fallback,
                system_prompt=system_prompt,
                temperature=effective_temperature,
                effective_max_tokens=effective_max_tokens,
                platform_retry_max=platform_retry_max,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                call_id=call_id,
                event_emitter=event_emitter,
                factory_semantic_identity=factory_semantic_identity,
            )
            response = ladder.response
            active_request = ladder.active_request
            profile = ladder.profile
            prepared = ladder.prepared
            model = ladder.model
            native_tool_fallback = ladder.native_tool_fallback
            native_response_fallback = ladder.native_response_fallback
            is_response_ok = ladder.is_response_ok
            response_error = ladder.response_error
            if is_response_ok:
                response_error = _required_tool_not_called_error(
                    prepared=prepared,
                    active_request=active_request,
                    response=response,
                    profile=profile,
                )
                if response_error:
                    is_response_ok = False

            if not is_response_ok:
                return self._build_call_error_response(
                    prepared=prepared,
                    active_request=active_request,
                    response_error=response_error,
                    profile=profile,
                    native_tool_fallback=native_tool_fallback,
                    native_response_fallback=native_response_fallback,
                    allow_native_tool_text_fallback=allow_native_tool_text_fallback,
                    model=model,
                    role_id=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    call_id=call_id,
                    event_emitter=event_emitter,
                    start_time=start_time,
                )

            return self._finalize_call_response(
                cache=cache,
                prepared=prepared,
                active_request=active_request,
                response=response,
                cache_eligible=cache_eligible,
                prompt_fingerprint=prompt_fingerprint,
                temperature=effective_temperature,
                model=model,
                profile=profile,
                prompt_tokens=prompt_tokens,
                turn_round=turn_round,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                call_id=call_id,
                event_emitter=event_emitter,
                start_time=start_time,
            )

        except asyncio.CancelledError:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._emit_call_error_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                error_category=ERROR_CATEGORY_CANCELLED,
                error_message="call_cancelled",
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata=_with_context_os_audit(
                    _with_optional_final_request_context_audit(
                        {
                            "error_type": "CancelledError",
                            "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request)
                            if prepared
                            else None,
                        },
                        prepared=prepared,
                        active_request=active_request or (prepared.ai_request if prepared else None),
                        profile=profile,
                    ),
                    prepared,
                ),
            )
            raise

        except (ImportError, AttributeError, TypeError, ValueError) as e:
            logger.error(f"LLM call failed: {e}")
            return self._call_exception_response(
                e,
                prepared=prepared,
                active_request=active_request or (prepared.ai_request if prepared else None),
                profile=profile,
                model=model,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                call_id=call_id,
                event_emitter=event_emitter,
                start_time=start_time,
            )

        except RuntimeError as e:
            logger.exception(f"LLM call unexpected error: {e}")
            if isinstance(e, _FactorySemanticDispatchNotEnabledError):
                return self._call_exception_response(
                    e,
                    prepared=prepared,
                    active_request=prepared.ai_request if prepared else None,
                    profile=profile,
                    model=model,
                    role_id=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    call_id=call_id,
                    event_emitter=event_emitter,
                    start_time=start_time,
                )
            fallback_response = await self._try_retryable_exception_role_binding_fallback(
                exc=e,
                request_preparer=request_preparer,
                executor=executor,
                prepared=prepared,
                active_request=active_request or (prepared.ai_request if prepared else None),
                profile=profile,
                context=context,
                system_prompt=system_prompt,
                temperature=effective_temperature,
                effective_max_tokens=effective_max_tokens,
                response_model=response_model,
                platform_retry_max=platform_retry_max,
                model=model,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                turn_round=turn_round,
                call_id=call_id,
                event_emitter=event_emitter,
                prompt_tokens=prompt_tokens,
                start_time=start_time,
                factory_semantic_identity=factory_semantic_identity,
            )
            if fallback_response is not None:
                return fallback_response
            return self._call_exception_response(
                e,
                prepared=prepared,
                active_request=active_request or (prepared.ai_request if prepared else None),
                profile=profile,
                model=model,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                call_id=call_id,
                event_emitter=event_emitter,
                start_time=start_time,
            )

    def _call_exception_response(
        self,
        exc: BaseException,
        *,
        prepared: PreparedLLMRequest | None,
        active_request: Any,
        profile: RoleProfile,
        model: str,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> LLMResponse:
        """Shared builder for the call-phase return-except arms (NOT CancelledError).

        Emits the call_error event and returns the failure ``LLMResponse`` with
        byte-identical metadata. The caller is responsible for the distinct log
        severity (``logger.error`` vs ``logger.exception``) before invoking this.
        """
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        error_category = classify_error(str(exc))
        self._emit_call_error_event(
            event_emitter=event_emitter,
            role=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=model,
            error_category=error_category,
            error_message=str(exc),
            call_id=call_id,
            elapsed_ms=elapsed_ms,
            metadata=_with_context_os_audit(
                _with_optional_final_request_context_audit(
                    {
                        "error_type": type(exc).__name__,
                        "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request)
                        if prepared
                        else None,
                    },
                    prepared=prepared,
                    active_request=active_request,
                    profile=profile,
                ),
                prepared,
            ),
        )
        return LLMResponse(
            content="",
            error=f"LLM call failed: {exc}",
            error_category=error_category,
            metadata=_with_context_os_audit(
                _with_optional_final_request_context_audit(
                    {
                        "run_id": run_id,
                        "workspace": self.workspace,
                        "attempt": attempt,
                        "elapsed_ms": round(elapsed_ms, 2),
                    },
                    prepared=prepared,
                    active_request=active_request,
                    profile=profile,
                ),
                prepared,
            ),
        )

    @staticmethod
    def _is_cache_eligible(
        *,
        prepared: PreparedLLMRequest,
        response_model: type | None,
    ) -> bool:
        """Cache is only safe for plain-text, no-tools turns."""
        if prepared.factory_dispatch_port is not None:
            return False
        if response_model is not None:
            return False
        if prepared.native_tool_mode != "disabled":
            return False
        if prepared.response_format_mode != "plain_text":
            return False
        return not prepared.native_tool_schemas

    @staticmethod
    def _is_stream_cancel_requested(context: Any) -> bool:
        """Check if stream cancellation was requested."""
        override = getattr(context, "context_override", None) if context else None
        if isinstance(override, dict) and override.get("stream_cancelled"):
            return True
        return bool(getattr(context, "stream_cancelled", False))

    # ========================================================================
    # Event emission delegates
    # ========================================================================

    def _emit_call_error_event(self, **kwargs: Any) -> None:
        """Delegate to LLMEventEmitter.emit_call_error_event."""
        self._fill_provider_from_metadata(kwargs)
        self._event_emitter.emit_call_error_event(**kwargs)

    def _emit_call_start_event(self, **kwargs: Any) -> None:
        """Delegate to LLMEventEmitter.emit_call_start_event."""
        self._fill_provider_from_metadata(kwargs)
        self._event_emitter.emit_call_start_event(**kwargs)

    def _emit_call_end_event(self, **kwargs: Any) -> None:
        """Delegate to LLMEventEmitter.emit_call_end_event."""
        self._event_emitter.emit_call_end_event(**kwargs)

    def _emit_call_retry_event(self, **kwargs: Any) -> None:
        """Delegate to LLMEventEmitter.emit_call_retry_event."""
        self._fill_provider_from_metadata(kwargs)
        self._event_emitter.emit_call_retry_event(**kwargs)

    @staticmethod
    def _fill_provider_from_metadata(kwargs: dict[str, Any]) -> None:
        if kwargs.get("provider"):
            return
        metadata = kwargs.get("metadata")
        if not isinstance(metadata, dict):
            return
        provider = metadata.get("provider") or metadata.get("provider_id") or metadata.get("to_provider")
        if provider:
            kwargs["provider"] = str(provider)
