"""Structured-output call mixins for LLMInvoker."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from ..context_audit import (
    build_final_request_context_audit_for_request,
)
from ..error_handling import (
    ERROR_CATEGORY_CANCELLED,
    classify_error,
    is_response_format_unsupported,
)
from ..factory_dispatch_propagation import (
    enforce_factory_aware_final_request_evidence_coverage,
)
from ..helpers import (
    extract_json_from_text,
    resolve_tool_call_provider,
)
from ..invoker_phases import read_response_status
from ..request_preparer import LLMRequestPreparer
from ..response_types import PreparedLLMRequest, StructuredLLMResponse
from ..stream_handler import (
    normalize_stream_chunk,  # noqa: F401
)

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
    from polaris.cells.roles.profile.public.service import RoleProfile

from ._helpers import (
    INSTRUCTOR_AVAILABLE,
    _enforce_factory_semantic_zero_transport,
    _final_request_context_tokens,
    _invoke_executor_with_factory_dispatch,
    _invoker_owned_factory_semantic_identity,
    _reprepare_semantic_retry,
    _store_call_start_context_snapshot,
    _with_context_os_audit,
    _with_final_request_context_audit,
    _with_optional_final_request_context_audit,
)

logger = logging.getLogger(__name__)

if INSTRUCTOR_AVAILABLE:
    from ._helpers import create_structured_client


class _InvokerStructuredMixin:
    """call_structured and instructor / response_format fallbacks."""

    __slots__ = ()

    if TYPE_CHECKING:
        workspace: str
        _formatter: Any
        _model_catalog: Any

        def _emit_call_end_event(self, **kwargs: Any) -> None: ...
        def _emit_call_error_event(self, **kwargs: Any) -> None: ...
        def _emit_call_retry_event(self, **kwargs: Any) -> None: ...
        def _emit_call_start_event(self, **kwargs: Any) -> None: ...
        def _emit_required_tool_retry_request_audit(self, *args: Any, **kwargs: Any) -> Any: ...
        def _extract_context_snapshot_ref(self, request: Any) -> str | None: ...
        def _get_executor(self) -> Any: ...

    # ========================================================================
    # Structured call (migrated from call_structured.py)
    # ========================================================================

    async def _try_native_response_format_structured(
        self,
        *,
        request_preparer: LLMRequestPreparer,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        response_model: type,
        model: str,
        prompt_tokens: int,
        turn_round: int,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> StructuredLLMResponse | None:
        """Structured strategy 1: native response_format.

        Returns a ``StructuredLLMResponse`` on success, or ``None`` to fall
        through to the next strategy. A non-``response_format_unsupported`` error
        is raised as ``RuntimeError`` then swallowed-and-warned (still falling
        through), preserving the original two-level control flow.
        """
        if not prepared.native_response_format:
            return None
        try:
            executor = self._get_executor()
            response = await _invoke_executor_with_factory_dispatch(
                executor=executor,
                prepared=prepared,
                request=prepared.ai_request,
                profile=profile,
            )
            if bool(getattr(response, "ok", True)):
                content = str(getattr(response, "output", "") or "")
                if not content.strip() and isinstance(getattr(response, "raw", None), dict):
                    content = json.dumps(response.raw, ensure_ascii=False)
                data = extract_json_from_text(content)
                validated = response_model(**data)
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                event_metadata = _with_final_request_context_audit(
                    {
                        "structured": True,
                        "native_response_format": True,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "compression_applied": prepared.context_result.compression_applied
                        if prepared.context_result
                        else False,
                        "turn_round": turn_round,
                        "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
                    },
                    prepared=prepared,
                    active_request=prepared.ai_request,
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
                    model=str(getattr(response, "model", "") or model),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=len(content) // 2,
                    call_id=call_id,
                    context_tokens_after=final_context_tokens,
                    compression_strategy=prepared.context_result.compression_strategy
                    if prepared.context_result
                    else None,
                    response_content=content,
                    metadata=_with_context_os_audit(event_metadata, prepared),
                )
                return StructuredLLMResponse(
                    data=validated.model_dump(),
                    raw_content=content,
                    token_estimate=prepared.context_result.token_estimate + len(content) // 2
                    if prepared.context_result
                    else len(content) // 2,
                    metadata=_with_context_os_audit(
                        _with_final_request_context_audit(
                            {
                                "native_response_format": True,
                                "response_format_mode": prepared.response_format_mode,
                                "elapsed_ms": round(elapsed_ms, 2),
                                "turn_round": turn_round,
                            },
                            prepared=prepared,
                            active_request=prepared.ai_request,
                            profile=profile,
                        ),
                        prepared,
                    ),
                )
            response_error = str(getattr(response, "error", "") or "").strip()
            normalized_error = response_error or "structured_llm_call_failed"
            fallback_request = request_preparer._build_structured_fallback_request(
                prepared=prepared,
                profile=profile,
                response_model=response_model,
            )
            retry_metadata = _with_final_request_context_audit(
                {
                    "structured": True,
                    "native_response_format": True,
                    "retry_decision": "structured_response_format_fallback",
                    "error_category": classify_error(normalized_error),
                    "error_message": normalized_error,
                    "context_snapshot_ref": self._extract_context_snapshot_ref(fallback_request),
                },
                prepared=prepared,
                active_request=fallback_request,
                profile=profile,
            )
            self._emit_call_retry_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                call_id=call_id,
                retry_decision="structured_response_format_fallback",
                backoff_seconds=0.0,
                metadata=_with_context_os_audit(retry_metadata, prepared),
            )
            if not is_response_format_unsupported(normalized_error):
                raise RuntimeError(normalized_error)
        except RuntimeError as e:
            logger.warning("Native structured response_format call failed: %s", e)
        return None

    async def _try_instructor_structured(
        self,
        *,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        messages: list[dict[str, str]],
        response_model: type,
        model: str,
        temperature: float,
        max_tokens: int,
        max_retries: int,
        prompt_tokens: int,
        turn_round: int,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> StructuredLLMResponse | None:
        """Structured strategy 2: Instructor ``create_structured``.

        Returns a ``StructuredLLMResponse`` on success, or ``None`` (after a
        warned ``RuntimeError``) to fall through to the deterministic fallback.
        """
        try:
            if prepared.factory_dispatch_port is not None:
                raise RuntimeError("factory_role_instructor_direct_sdk_not_enabled")
            provider = resolve_tool_call_provider(
                provider_id=str(getattr(profile, "provider_id", "") or ""), model=model
            )
            structured_client = create_structured_client(provider=provider, enable_instructor=True, async_mode=True)
            result: Any = await structured_client.create_structured(
                messages=messages,
                response_model=response_model,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                max_retries=max_retries,
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            event_metadata = _with_final_request_context_audit(
                {
                    "structured": True,
                    "instructor_used": True,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "compression_applied": prepared.context_result.compression_applied
                    if prepared.context_result
                    else False,
                    "turn_round": turn_round,
                    "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
                },
                prepared=prepared,
                active_request=prepared.ai_request,
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
                model=model,
                call_id=call_id,
                completion_tokens=len(result.model_dump_json()) // 2,
                context_tokens_after=final_context_tokens,
                compression_strategy=prepared.context_result.compression_strategy if prepared.context_result else None,
                response_content=result.model_dump_json(),
                metadata=_with_context_os_audit(event_metadata, prepared),
            )
            return StructuredLLMResponse(
                data=result.model_dump(),
                raw_content=result.model_dump_json(),
                token_estimate=prompt_tokens + len(result.model_dump_json()) // 2,
                metadata=_with_context_os_audit(
                    _with_final_request_context_audit(
                        {
                            "model": model,
                            "instructor_used": True,
                            "elapsed_ms": round(elapsed_ms, 2),
                            "run_id": run_id,
                            "workspace": self.workspace,
                            "attempt": attempt,
                            "turn_round": turn_round,
                        },
                        prepared=prepared,
                        active_request=prepared.ai_request,
                        profile=profile,
                    ),
                    prepared,
                ),
            )
        except RuntimeError as e:
            logger.warning(f"Instructor structured call failed: {e}, falling back")
        return None

    async def _run_structured_fallback(
        self,
        *,
        request_preparer: LLMRequestPreparer,
        prepared: PreparedLLMRequest,
        profile: RoleProfile,
        response_model: type,
        model: str,
        prompt_tokens: int,
        turn_round: int,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        call_id: str,
        event_emitter: Any | None,
        start_time: float,
    ) -> StructuredLLMResponse:
        """Structured strategy 3: deterministic text-JSON fallback.

        Builds the structured fallback request, invokes, and either returns the
        not-ok error response, the validated success response, or the parse-fail
        ``validation_fail`` response. This strategy ALWAYS returns.
        """
        ai_request = request_preparer._build_structured_fallback_request(
            prepared=prepared, profile=profile, response_model=response_model
        )
        executor = self._get_executor()
        prepared, ai_request = await _reprepare_semantic_retry(
            request_preparer=request_preparer,
            prepared=prepared,
            request=ai_request,
            profile=profile,
        )
        await self._emit_required_tool_retry_request_audit(
            prepared=prepared,
            request=ai_request,
            request_profile=profile,
            role_id=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=model,
            call_id=call_id,
            event_emitter=event_emitter,
            retry_decision="structured_text_fallback",
        )
        response = await _invoke_executor_with_factory_dispatch(
            executor=executor,
            prepared=prepared,
            request=ai_request,
            profile=profile,
        )
        is_response_ok, response_error = read_response_status(response)
        response_format_mode = ""
        if isinstance(getattr(ai_request, "context", None), dict):
            response_format_mode = str(ai_request.context.get("response_format_mode", "") or "")
        if not is_response_ok:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            classified = classify_error(response_error)
            normalized_error = response_error or "structured_llm_call_failed"
            error_metadata = _with_final_request_context_audit(
                {
                    "structured": True,
                    "response_format_mode": response_format_mode,
                    "context_snapshot_ref": self._extract_context_snapshot_ref(ai_request),
                },
                prepared=prepared,
                active_request=ai_request,
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
                error_message=normalized_error,
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata=_with_context_os_audit(error_metadata, prepared),
            )
            return StructuredLLMResponse(
                data={},
                raw_content="",
                error=normalized_error,
                error_category=classified,
                metadata=_with_context_os_audit(
                    _with_final_request_context_audit(
                        {
                            "model": model,
                            "response_format_mode": response_format_mode,
                            "elapsed_ms": round(elapsed_ms, 2),
                            "run_id": run_id,
                            "workspace": self.workspace,
                            "attempt": attempt,
                        },
                        prepared=prepared,
                        active_request=ai_request,
                        profile=profile,
                    ),
                    prepared,
                ),
            )

        content = str(getattr(response, "output", "") or "")
        if not content.strip() and isinstance(getattr(response, "raw", None), dict):
            try:
                content = json.dumps(response.raw, ensure_ascii=False)
            except (RuntimeError, ValueError):
                content = str(getattr(response, "output", "") or "")

        try:
            data = extract_json_from_text(content)
            validated = response_model(**data)
            validated_data = validated.model_dump() if hasattr(validated, "model_dump") else dict(validated)
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            event_metadata = _with_final_request_context_audit(
                {
                    "structured": True,
                    "instructor_used": False,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "compression_applied": prepared.context_result.compression_applied
                    if prepared.context_result
                    else False,
                    "turn_round": turn_round,
                    "context_snapshot_ref": self._extract_context_snapshot_ref(ai_request),
                },
                prepared=prepared,
                active_request=ai_request,
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
                model=model,
                call_id=call_id,
                completion_tokens=len(content) // 2,
                context_tokens_after=final_context_tokens,
                compression_strategy=prepared.context_result.compression_strategy if prepared.context_result else None,
                response_content=content,
                metadata=_with_context_os_audit(event_metadata, prepared),
            )
            return StructuredLLMResponse(
                data=validated_data,
                raw_content=content,
                token_estimate=prompt_tokens + len(content) // 2,
                metadata=_with_context_os_audit(
                    _with_final_request_context_audit(
                        {
                            "model": model,
                            "instructor_used": False,
                            "elapsed_ms": round(elapsed_ms, 2),
                            "run_id": run_id,
                            "workspace": self.workspace,
                            "attempt": attempt,
                            "turn_round": turn_round,
                            "context_snapshot_ref": self._extract_context_snapshot_ref(ai_request),
                        },
                        prepared=prepared,
                        active_request=ai_request,
                        profile=profile,
                    ),
                    prepared,
                ),
            )
        except (RuntimeError, ValueError) as parse_error:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            error_msg = f"Failed to parse structured output: {parse_error}"
            self._emit_call_error_event(
                event_emitter=event_emitter,
                role=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                model=model,
                error_category="validation_fail",
                error_message=error_msg,
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata=_with_context_os_audit(
                    _with_final_request_context_audit(
                        {
                            "structured": True,
                            "response_format_mode": response_format_mode,
                            "context_snapshot_ref": self._extract_context_snapshot_ref(ai_request),
                        },
                        prepared=prepared,
                        active_request=ai_request,
                        profile=profile,
                    ),
                    prepared,
                ),
            )
            return StructuredLLMResponse(
                data={},
                raw_content=content,
                error=error_msg,
                error_category="validation_fail",
                validation_errors=[str(parse_error)],
                metadata=_with_context_os_audit(
                    _with_final_request_context_audit(
                        {
                            "model": model,
                            "elapsed_ms": round(elapsed_ms, 2),
                            "run_id": run_id,
                            "workspace": self.workspace,
                            "attempt": attempt,
                            "response_format_mode": response_format_mode,
                            "context_snapshot_ref": self._extract_context_snapshot_ref(ai_request),
                        },
                        prepared=prepared,
                        active_request=ai_request,
                        profile=profile,
                    ),
                    prepared,
                ),
            )

    async def call_structured(
        self,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        response_model: type,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        max_retries: int = 3,
        prompt_fingerprint: str | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        attempt: int = 0,
        turn_round: int = 0,
        event_emitter: Any | None = None,
    ) -> StructuredLLMResponse:
        """Invoke LLM with structured output validation."""
        call_id = uuid.uuid4().hex
        factory_semantic_identity = _invoker_owned_factory_semantic_identity(
            run_id=run_id,
            turn_round=turn_round,
            call_id=call_id,
        )
        run_id = run_id or f"llm_struct_{call_id}"
        task_id = task_id or getattr(context, "task_id", None)
        role_id = str(getattr(profile, "role_id", "unknown") or "unknown")
        model = profile.model or "default"
        from .helpers import resolve_max_tokens

        context_override = getattr(context, "context_override", None)
        effective_max_tokens = resolve_max_tokens(max_tokens, context_override)
        effective_temperature = temperature

        start_time = time.perf_counter()
        prepared: PreparedLLMRequest | None = None

        try:
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
                platform_retry_max=max_retries,
                factory_semantic_identity=factory_semantic_identity,
            )
            _enforce_factory_semantic_zero_transport(prepared)
            await _store_call_start_context_snapshot(
                workspace=self.workspace,
                prepared=prepared,
                profile=profile,
                run_id=run_id,
                call_id=call_id,
            )
            messages = prepared.messages
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
                messages=messages,
                metadata=_with_context_os_audit(
                    {
                        "structured": True,
                        "response_model": response_model.__name__,
                        "instructor_available": INSTRUCTOR_AVAILABLE,
                        "native_tool_mode": prepared.native_tool_mode,
                        "response_format_mode": prepared.response_format_mode,
                        "compression_applied": context_result.compression_applied if context_result else False,
                        "turn_round": turn_round,
                        "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request),
                        "context_tokens_after": final_context_tokens,
                        "contextTokens": final_context_tokens,
                        "final_request_context_audit": final_context_audit,
                    },
                    prepared,
                ),
            )

            # Try native response_format
            native_result = await self._try_native_response_format_structured(
                request_preparer=request_preparer,
                prepared=prepared,
                profile=profile,
                response_model=response_model,
                model=model,
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
            if native_result is not None:
                return native_result

            # Try Instructor
            if INSTRUCTOR_AVAILABLE:
                instructor_result = await self._try_instructor_structured(
                    prepared=prepared,
                    profile=profile,
                    messages=messages,
                    response_model=response_model,
                    model=model,
                    temperature=effective_temperature,
                    max_tokens=effective_max_tokens,
                    max_retries=max_retries,
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
                if instructor_result is not None:
                    return instructor_result

            # Fallback
            return await self._run_structured_fallback(
                request_preparer=request_preparer,
                prepared=prepared,
                profile=profile,
                response_model=response_model,
                model=model,
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
                error_message="structured_call_cancelled",
                call_id=call_id,
                elapsed_ms=elapsed_ms,
                metadata=_with_context_os_audit(
                    _with_optional_final_request_context_audit(
                        {
                            "structured": True,
                            "error_type": "CancelledError",
                            "context_snapshot_ref": self._extract_context_snapshot_ref(prepared.ai_request)
                            if prepared
                            else None,
                        },
                        prepared=prepared,
                        active_request=prepared.ai_request if prepared else None,
                        profile=profile,
                    ),
                    prepared,
                ),
            )
            raise

        except RuntimeError as e:
            return self._structured_exception_response(
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

    def _structured_exception_response(
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
    ) -> StructuredLLMResponse:
        """Shared builder for the structured RuntimeError except arm (NOT CancelledError).

        Emits the call_error event and returns the failure
        ``StructuredLLMResponse`` with byte-identical structured metadata.
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
                        "structured": True,
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
        return StructuredLLMResponse(
            data={},
            error=f"Structured LLM call failed: {exc}",
            error_category=error_category,
            metadata=_with_context_os_audit(
                _with_optional_final_request_context_audit(
                    {
                        "model": model,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "run_id": run_id,
                        "workspace": self.workspace,
                        "attempt": attempt,
                    },
                    prepared=prepared,
                    active_request=active_request,
                    profile=profile,
                ),
                prepared,
            ),
        )
