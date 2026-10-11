"""Role-binding fallback mixins for _InvokerBindingMixin."""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass, fields
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FactoryRoleSemanticRequestIdentityV1,
)
from polaris.cells.roles.profile.public.service import RoleProfile
from polaris.kernelone.llm.runtime_config import (
    RoleBindingSlot,
    clear_role_provider_override,
    get_role_binding_candidates,
    get_role_binding_override,
    get_role_provider_override,
    is_role_binding_healthy,
    mark_role_binding_unhealthy,
    set_role_binding_override,
    set_role_provider_override,
)

from ...llm_cache import get_global_llm_cache
from ..context_audit import (
    build_final_request_context_audit_for_request,
)
from ..error_handling import (
    classify_error,
    is_retryable_error,
)
from ..invoker_phases import read_response_status
from ..request_preparer import LLMRequestPreparer
from ..response_types import LLMResponse, PreparedLLMRequest
from ..stream_handler import (
    normalize_stream_chunk,  # noqa: F401
)

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest

from ._helpers import (
    _invoke_executor_with_factory_dispatch,
    _profile_lacks_forced_tool_choice,
    _refreeze_factory_semantic_identity,
    _required_tool_not_called_error,
    _required_tools_from_final_request_audit,
    _RoleBindingFallbackFailure,
    _store_active_request_context_snapshot,
    _with_final_request_context_audit,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SelectedBindingRoleProfile(RoleProfile):
    """Live selection evidence; the dispatch port freezes this safe identity."""

    selected_role_binding_json: str = ""
    role_route_obligations_json: str = ""
    route_fallback_authorization_json: str = ""


class _InvokerBindingMixin:
    """Binding selection, health marks, and multi-slot fallback."""

    __slots__ = ()

    if TYPE_CHECKING:
        workspace: str

        def _build_call_error_response(self, *args: Any, **kwargs: Any) -> Any: ...
        def _emit_call_retry_event(self, **kwargs: Any) -> None: ...
        def _emit_required_tool_retry_request_audit(self, *args: Any, **kwargs: Any) -> Any: ...
        def _extract_context_snapshot_ref(self, request: Any) -> str | None: ...
        def _finalize_call_response(self, *args: Any, **kwargs: Any) -> Any: ...
        def _retry_required_tool_if_missing(self, *args: Any, **kwargs: Any) -> Any: ...

    @staticmethod
    def _role_allows_binding_fallback(role_id: str) -> bool:
        return role_id.strip().lower() in {"pm", "chief_engineer", "qa", "architect", "director"}

    @staticmethod
    def _profile_for_binding(profile: RoleProfile, slot: RoleBindingSlot) -> RoleProfile:
        selection = json.dumps(
            {
                "schema_version": "llm.selected_role_binding.v1",
                "role": slot.role_id,
                "provider_id": slot.provider_id,
                "model": slot.model,
                "binding_id": slot.binding_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            values = {item.name: getattr(profile, item.name) for item in fields(RoleProfile)}
            values.update(
                provider_id=slot.provider_id,
                model=slot.model,
                selected_role_binding_json=selection,
                role_route_obligations_json=str(getattr(profile, "role_route_obligations_json", "") or ""),
                route_fallback_authorization_json=str(getattr(profile, "route_fallback_authorization_json", "") or ""),
            )
            return _SelectedBindingRoleProfile(**values)
        except (AttributeError, TypeError, ValueError):
            clone = SimpleNamespace(**vars(profile))
            clone.provider_id = slot.provider_id
            clone.model = slot.model
            clone.selected_role_binding_json = selection
            return clone  # type: ignore[return-value]

    @staticmethod
    def _fallback_slots_for_role(role_id: str, profile: RoleProfile) -> tuple[RoleBindingSlot, ...]:
        if not _InvokerBindingMixin._role_allows_binding_fallback(role_id):
            return ()
        try:
            slots = get_role_binding_candidates(role_id)
        except (RuntimeError, ValueError, TypeError):
            return ()
        current_provider = str(getattr(profile, "provider_id", "") or "").strip()
        current_model = str(getattr(profile, "model", "") or "").strip()
        candidates: list[RoleBindingSlot] = []
        seen: set[tuple[str, str, str]] = set()
        for slot in slots:
            provider_id = str(slot.provider_id or "").strip()
            model = str(slot.model or "").strip()
            if not provider_id or not model:
                continue
            if provider_id == current_provider and model == current_model:
                continue
            key = (provider_id, model, str(slot.binding_id or ""))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(slot)
        return tuple(candidates)

    @staticmethod
    def _profile_uses_healthy_binding(role_id: str, profile: RoleProfile) -> bool:
        binding = get_role_binding_override(role_id)
        provider_id = str((binding or {}).get("provider_id") or getattr(profile, "provider_id", "") or "").strip()
        model = str((binding or {}).get("model") or getattr(profile, "model", "") or "").strip()
        binding_id = str((binding or {}).get("binding_id") or "").strip()
        return is_role_binding_healthy(
            role_id,
            provider_id=provider_id,
            model=model,
            binding_id=binding_id,
        )

    @staticmethod
    def _with_route_obligations(
        profile: RoleProfile, role_id: str, candidates: tuple[RoleBindingSlot, ...]
    ) -> RoleProfile:
        if getattr(profile, "role_route_obligations_json", "") or not candidates:
            return profile
        declared = candidates if role_id == "director" else candidates[:1]
        rows = list(
            {
                (slot.provider_id, slot.model, slot.binding_id): {
                    "provider_id": slot.provider_id,
                    "model": slot.model,
                    "binding_id": slot.binding_id,
                }
                for slot in declared
            }.values()
        )
        clone = SimpleNamespace(**vars(profile))
        clone.role_route_obligations_json = json.dumps(
            {"source": "roles.kernel.binding_selection", "expected": rows, "skips": []},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return clone  # type: ignore[return-value]

    @staticmethod
    def _profile_for_selected_route(role_id: str, profile: RoleProfile) -> RoleProfile:
        """Carry the existing default/worker route without selecting a fallback.

        The default is the declared primary slot, never a tuple search. A route
        differing from that primary (or the explicit worker override) has no
        binding provenance here and remains unqualified. No health selection or
        provider/model change is performed for observation.
        """
        if getattr(profile, "selected_role_binding_json", ""):
            return profile
        candidates = get_role_binding_candidates(role_id)
        override = get_role_binding_override(role_id)
        if override:
            slot = RoleBindingSlot(
                role_id=role_id,
                provider_id=str(override.get("provider_id") or ""),
                model=str(override.get("model") or ""),
                binding_id=str(override.get("binding_id") or ""),
            )
        else:
            if not candidates:
                return profile
            slot = candidates[0]
        if slot.role_id != role_id or slot.provider_id != profile.provider_id or slot.model != profile.model:
            return profile
        profile = _InvokerBindingMixin._with_route_obligations(profile, role_id, (slot,) if override else candidates)
        return _InvokerBindingMixin._profile_for_binding(profile, slot)

    @staticmethod
    def _profile_for_healthy_binding(role_id: str, profile: RoleProfile) -> RoleProfile:
        if getattr(profile, "selected_role_binding_json", ""):
            return profile
        candidates = get_role_binding_candidates(role_id)
        binding = get_role_binding_override(role_id)
        if binding:
            provider_id = str(binding.get("provider_id") or "").strip()
            model = str(binding.get("model") or "").strip()
            binding_id = str(binding.get("binding_id") or "").strip()
            fanout_locked = str(binding.get("_fanout_locked") or "").strip().lower() == "true"
            if provider_id and model:
                slot = RoleBindingSlot(
                    role_id=role_id,
                    provider_id=provider_id,
                    model=model,
                    binding_id=binding_id,
                )
                if fanout_locked:
                    return _InvokerBindingMixin._profile_for_binding(
                        _InvokerBindingMixin._with_route_obligations(profile, role_id, (slot,)), slot
                    )
                if is_role_binding_healthy(
                    role_id,
                    provider_id=provider_id,
                    model=model,
                    binding_id=binding_id,
                ):
                    return _InvokerBindingMixin._profile_for_binding(
                        _InvokerBindingMixin._with_route_obligations(profile, role_id, (slot,)), slot
                    )

        if _InvokerBindingMixin._profile_uses_healthy_binding(role_id, profile):
            # The configured primary is an actual slot, rather than a binding
            # reconstructed from model text. Preserve profile routing when it
            # does not correspond to that selected primary; such calls cannot
            # provide historical binding qualification.
            if candidates:
                primary = candidates[0]
                if primary.provider_id == profile.provider_id and primary.model == profile.model:
                    return _InvokerBindingMixin._profile_for_binding(
                        _InvokerBindingMixin._with_route_obligations(profile, role_id, (primary,)), primary
                    )
            return profile
        for slot in candidates:
            if is_role_binding_healthy(
                role_id,
                provider_id=slot.provider_id,
                model=slot.model,
                binding_id=slot.binding_id,
            ):
                return _InvokerBindingMixin._profile_for_binding(
                    _InvokerBindingMixin._with_route_obligations(profile, role_id, (slot,)), slot
                )
        return profile

    @staticmethod
    def _mark_profile_binding_unhealthy(role_id: str, profile: RoleProfile) -> None:
        binding = get_role_binding_override(role_id)
        provider_id = str((binding or {}).get("provider_id") or getattr(profile, "provider_id", "") or "").strip()
        model = str((binding or {}).get("model") or getattr(profile, "model", "") or "").strip()
        binding_id = str((binding or {}).get("binding_id") or "").strip()
        mark_role_binding_unhealthy(
            role_id,
            provider_id=provider_id,
            model=model,
            binding_id=binding_id or None,
        )

    @staticmethod
    def _restore_role_binding_override(
        role_id: str,
        previous_binding: dict[str, str] | None,
        previous_provider: str | None,
    ) -> None:
        if previous_binding:
            set_role_binding_override(
                role_id,
                provider_id=str(previous_binding.get("provider_id") or ""),
                model=str(previous_binding.get("model") or ""),
                binding_id=str(previous_binding.get("binding_id") or "") or None,
            )
            return
        if previous_provider:
            set_role_provider_override(role_id, previous_provider)
            return
        clear_role_provider_override(role_id)

    async def _try_role_binding_fallback(
        self,
        *,
        request_preparer: LLMRequestPreparer,
        profile: RoleProfile,
        system_prompt: str,
        context: ContextRequest,
        temperature: float,
        max_tokens: int,
        response_model: type | None,
        platform_retry_max: int,
        executor: Any,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        model: str,
        call_id: str,
        event_emitter: Any | None,
        original_error: str,
        failure_sink: list[_RoleBindingFallbackFailure] | None = None,
        factory_semantic_identity: FactoryRoleSemanticRequestIdentityV1 | None = None,
    ) -> tuple[RoleProfile, PreparedLLMRequest, Any, Any] | None:
        self._mark_profile_binding_unhealthy(role_id, profile)
        fallback_slots = self._fallback_slots_for_role(role_id, profile)
        if not fallback_slots:
            return None

        for slot in fallback_slots:
            fallback_prepared: PreparedLLMRequest | None = None
            fallback_active_request: Any | None = None
            fallback_error = ""

            previous_binding = get_role_binding_override(role_id)
            previous_provider = get_role_provider_override(role_id)
            set_role_binding_override(
                role_id,
                provider_id=slot.provider_id,
                model=slot.model,
                binding_id=slot.binding_id or None,
            )
            fallback_profile = self._profile_for_binding(profile, slot)
            from_selection = json.loads(str(getattr(profile, "selected_role_binding_json", "") or "{}"))
            if factory_semantic_identity is not None and from_selection:
                clone = SimpleNamespace(**vars(fallback_profile))
                clone.route_fallback_authorization_json = json.dumps(
                    {
                        "source": "roles.kernel.role_binding_fallback",
                        "from_request_freeze_id": factory_semantic_identity.request_freeze_id,
                        "from_binding": {key: from_selection[key] for key in ("provider_id", "model", "binding_id")},
                        "to_binding": {
                            "provider_id": slot.provider_id,
                            "model": slot.model,
                            "binding_id": slot.binding_id,
                        },
                        "trigger_error_category": classify_error(original_error),
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                fallback_profile = clone
            try:
                self._emit_call_retry_event(
                    event_emitter=event_emitter,
                    role=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    model=model,
                    provider=slot.provider_id,
                    call_id=call_id,
                    retry_decision="role_binding_fallback",
                    backoff_seconds=0.0,
                    metadata={
                        "from_provider": str(getattr(profile, "provider_id", "") or ""),
                        "from_model": str(getattr(profile, "model", "") or ""),
                        "to_provider": slot.provider_id,
                        "to_model": slot.model,
                        "binding_id": slot.binding_id,
                        "trigger_error_category": classify_error(original_error),
                    },
                )
                fallback_prepared = await request_preparer._prepare_llm_request(
                    profile=fallback_profile,
                    system_prompt=system_prompt,
                    context=context,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=False,
                    response_model=response_model,
                    platform_retry_max=platform_retry_max,
                    factory_semantic_identity=_refreeze_factory_semantic_identity(factory_semantic_identity),
                )
                fallback_active_request = fallback_prepared.ai_request
                fallback_required_tools = _required_tools_from_final_request_audit(
                    build_final_request_context_audit_for_request(
                        ai_request=fallback_prepared.ai_request,
                        prepared=fallback_prepared,
                        profile=fallback_profile,
                    )
                )
                if fallback_required_tools and _profile_lacks_forced_tool_choice(fallback_profile):
                    fallback_active_request = request_preparer._build_required_tool_text_fallback_request(
                        prepared=fallback_prepared,
                        profile=fallback_profile,
                        error_message="required_tool_not_called: required_tools=" + ",".join(fallback_required_tools),
                    )
                    if (
                        fallback_prepared.factory_dispatch_port is not None
                        and fallback_active_request is not fallback_prepared.ai_request
                    ):
                        fallback_prepared = await request_preparer._reprepare_factory_semantic_retry_request(
                            prepared=fallback_prepared,
                            request=fallback_active_request,
                            profile=fallback_profile,
                        )
                        fallback_active_request = fallback_prepared.ai_request
                    await self._emit_required_tool_retry_request_audit(
                        prepared=fallback_prepared,
                        request=fallback_active_request,
                        request_profile=fallback_profile,
                        role_id=role_id,
                        run_id=run_id,
                        task_id=task_id,
                        attempt=attempt,
                        model=slot.model,
                        call_id=call_id,
                        event_emitter=event_emitter,
                        retry_decision="required_tool_text_fallback",
                    )
                    fallback_response = await self._invoke_with_profile_binding(
                        executor=executor,
                        prepared=fallback_prepared,
                        request=fallback_active_request,
                        profile=fallback_profile,
                        role_id=role_id,
                    )
                    fallback_ok, fallback_error = read_response_status(fallback_response)
                    if fallback_ok:
                        fallback_error = _required_tool_not_called_error(
                            prepared=fallback_prepared,
                            active_request=fallback_prepared.ai_request,
                            response=fallback_response,
                            profile=fallback_profile,
                        )
                        if fallback_error:
                            fallback_error = f"required_tool_text_fallback_not_dispatched: {fallback_error}"
                    if fallback_ok and not fallback_error:
                        return (
                            fallback_profile,
                            fallback_prepared,
                            fallback_active_request,
                            fallback_response,
                        )
                    raise RuntimeError(fallback_error or "required_tool_text_fallback_failed")
                await _store_active_request_context_snapshot(
                    workspace=self.workspace,
                    active_request=fallback_prepared.ai_request,
                    prepared=fallback_prepared,
                    profile=fallback_profile,
                    run_id=run_id,
                    call_id=f"{call_id}-role_binding_fallback_request",
                )
                fallback_audit_metadata = _with_final_request_context_audit(
                    {
                        "fallback_request": True,
                        "retry_decision": "role_binding_fallback_request",
                        "from_provider": str(getattr(profile, "provider_id", "") or ""),
                        "from_model": str(getattr(profile, "model", "") or ""),
                        "to_provider": slot.provider_id,
                        "to_model": slot.model,
                        "binding_id": slot.binding_id,
                        "context_snapshot_ref": self._extract_context_snapshot_ref(fallback_prepared.ai_request),
                    },
                    prepared=fallback_prepared,
                    active_request=fallback_prepared.ai_request,
                    profile=fallback_profile,
                )
                self._emit_call_retry_event(
                    event_emitter=event_emitter,
                    role=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    model=slot.model,
                    provider=slot.provider_id,
                    call_id=call_id,
                    retry_decision="role_binding_fallback_request",
                    backoff_seconds=0.0,
                    metadata=fallback_audit_metadata,
                )
                fallback_response = await self._invoke_with_profile_binding(
                    executor=executor,
                    prepared=fallback_prepared,
                    request=fallback_prepared.ai_request,
                    profile=fallback_profile,
                    role_id=role_id,
                )
            except (RuntimeError, TypeError, ValueError) as exc:
                fallback_error = str(exc)
            else:
                fallback_ok = getattr(fallback_response, "ok", True)
                raw_error = getattr(fallback_response, "error", None)
                fallback_error = str(raw_error or "").strip() if raw_error is not None else ""
                if (bool(fallback_ok) if isinstance(fallback_ok, bool) else True) and not fallback_error:
                    return (
                        fallback_profile,
                        fallback_prepared,
                        fallback_prepared.ai_request,
                        fallback_response,
                    )
            finally:
                self._restore_role_binding_override(role_id, previous_binding, previous_provider)

            normalized_fallback_error = str(fallback_error or "").strip()
            if failure_sink is not None and fallback_prepared is not None and normalized_fallback_error:
                request = (
                    fallback_active_request if fallback_active_request is not None else fallback_prepared.ai_request
                )
                failure_sink.append(
                    _RoleBindingFallbackFailure(
                        profile=fallback_profile,
                        prepared=fallback_prepared,
                        active_request=request,
                        error=normalized_fallback_error,
                        model=slot.model,
                    )
                )
            fallback_category = classify_error(fallback_error)
            if is_retryable_error(fallback_category):
                mark_role_binding_unhealthy(
                    role_id,
                    provider_id=slot.provider_id,
                    model=slot.model,
                    binding_id=slot.binding_id or None,
                )
            else:
                return None

        return None

    async def _invoke_with_profile_binding(
        self,
        *,
        executor: Any,
        prepared: PreparedLLMRequest,
        request: Any,
        profile: RoleProfile,
        role_id: str,
    ) -> Any:
        """Invoke a retry request while preserving the selected provider/model."""

        previous_binding = get_role_binding_override(role_id)
        previous_provider = get_role_provider_override(role_id)
        provider_id = str(getattr(profile, "provider_id", "") or "").strip()
        model = str(getattr(profile, "model", "") or "").strip()
        previous_request_provider = getattr(request, "provider_id", None)
        previous_request_model = getattr(request, "model", None)
        if provider_id and model:
            set_role_binding_override(
                role_id,
                provider_id=provider_id,
                model=model,
            )
            with contextlib.suppress(AttributeError, TypeError):
                request.provider_id = provider_id
            with contextlib.suppress(AttributeError, TypeError):
                request.model = model
        try:
            return await _invoke_executor_with_factory_dispatch(
                executor=executor,
                prepared=prepared,
                request=request,
                profile=profile,
            )
        finally:
            with contextlib.suppress(AttributeError, TypeError):
                request.provider_id = previous_request_provider
            with contextlib.suppress(AttributeError, TypeError):
                request.model = previous_request_model
            self._restore_role_binding_override(role_id, previous_binding, previous_provider)

    async def _try_retryable_exception_role_binding_fallback(
        self,
        *,
        exc: RuntimeError,
        request_preparer: LLMRequestPreparer | None,
        executor: Any | None,
        prepared: PreparedLLMRequest | None,
        active_request: Any,
        profile: RoleProfile,
        context: ContextRequest,
        system_prompt: str,
        temperature: float,
        effective_max_tokens: int,
        response_model: type | None,
        platform_retry_max: int,
        model: str,
        role_id: str,
        run_id: str,
        task_id: str | None,
        attempt: int,
        turn_round: int,
        call_id: str,
        event_emitter: Any | None,
        prompt_tokens: int,
        start_time: float,
        factory_semantic_identity: FactoryRoleSemanticRequestIdentityV1 | None,
    ) -> LLMResponse | None:
        """Fallback to another role binding when a provider raises a retryable exception.

        Normal provider failures that return ``AIResponse(ok=False, error=...)``
        already flow through ``_run_fallback_ladder``. Some provider adapters
        raise ``RuntimeError`` for quota, 5xx, timeout, or circuit-breaker
        failures before an ``AIResponse`` exists. Those failures are still
        provider-route failures, so they should use the same role binding
        fallback path before the turn is marked failed.
        """

        if prepared is None or request_preparer is None or executor is None:
            return None
        error_category = classify_error(str(exc))
        if not is_retryable_error(error_category):
            return None

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
            original_error=str(exc),
            failure_sink=fallback_failures,
            factory_semantic_identity=factory_semantic_identity,
        )
        if fallback is None:
            if fallback_failures:
                latest_failure = fallback_failures[-1]
                return self._build_call_error_response(
                    prepared=latest_failure.prepared,
                    active_request=latest_failure.active_request,
                    response_error=latest_failure.error,
                    profile=latest_failure.profile,
                    native_tool_fallback=False,
                    native_response_fallback=False,
                    allow_native_tool_text_fallback=False,
                    model=latest_failure.model,
                    role_id=role_id,
                    run_id=run_id,
                    task_id=task_id,
                    attempt=attempt,
                    call_id=call_id,
                    event_emitter=event_emitter,
                    start_time=start_time,
                )
            return None

        fallback_profile, fallback_prepared, fallback_active_request, fallback_raw_response = fallback
        fallback_model = str(getattr(fallback_profile, "model", "") or model)
        is_response_ok, response_error = read_response_status(fallback_raw_response)
        native_tool_fallback = False
        (
            fallback_prepared,
            fallback_active_request,
            fallback_raw_response,
            is_response_ok,
            response_error,
            native_tool_fallback,
        ) = await self._retry_required_tool_if_missing(
            request_preparer=request_preparer,
            executor=executor,
            prepared=fallback_prepared,
            profile=fallback_profile,
            response=fallback_raw_response,
            active_request=fallback_active_request,
            response_error=response_error,
            is_response_ok=is_response_ok,
            native_tool_fallback=native_tool_fallback,
            role_id=role_id,
            run_id=run_id,
            task_id=task_id,
            attempt=attempt,
            model=fallback_model,
            call_id=call_id,
            event_emitter=event_emitter,
        )

        if not is_response_ok:
            return self._build_call_error_response(
                prepared=fallback_prepared,
                active_request=fallback_active_request,
                response_error=response_error,
                profile=fallback_profile,
                native_tool_fallback=native_tool_fallback,
                native_response_fallback=False,
                allow_native_tool_text_fallback=False,
                model=fallback_model,
                role_id=role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                call_id=call_id,
                event_emitter=event_emitter,
                start_time=start_time,
            )

        return self._finalize_call_response(
            cache=get_global_llm_cache(),
            prepared=fallback_prepared,
            active_request=fallback_active_request,
            response=fallback_raw_response,
            cache_eligible=False,
            prompt_fingerprint=None,
            temperature=temperature,
            model=fallback_model,
            profile=fallback_profile,
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
