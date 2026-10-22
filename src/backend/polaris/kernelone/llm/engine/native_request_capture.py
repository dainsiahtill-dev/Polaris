"""Transport observation for ordinary calls, separate from execution authority."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, AsyncContextManager, TypeVar

from polaris.kernelone.events.final_request_evidence import redact_provider_transport

from .contracts import AIRequest, ModelSpec, _deep_freeze_provider_value
from .internal.context_hash import validate_context_hash
from .invocation_budget import get_provider_invocation_budget
from .native_request_metrics import provider_native_request_metrics
from .provider_native_request import (
    build_anthropic_native_messages,
    build_openai_native_messages,
    convert_tool_choice_to_anthropic,
    convert_tools_to_anthropic,
)

_Result = TypeVar("_Result")
_Response = TypeVar("_Response")
NativeRequestObserver = Callable[[AIRequest, Mapping[str, Any]], None]
logger = logging.getLogger(__name__)


def verified_native_capture_audit(*, request: Any, workspace: str | None) -> dict[str, Any] | None:
    """Read actual ordinary capture evidence under the caller's workspace."""
    from polaris.kernelone.fs import KernelFileSystem
    from polaris.kernelone.fs.registry import get_default_adapter

    context = getattr(request, "context", None)
    if not isinstance(context, dict) or not isinstance(context.get("physical_request_capture"), dict):
        return None
    try:
        raw_ref = context.get("context_snapshot_ref")
        if not isinstance(raw_ref, str):
            return None
        ref = validate_context_hash(raw_ref)
        text = KernelFileSystem(workspace or ".", get_default_adapter()).read_text(f"runtime/contexts/{ref[:2]}/{ref}")
        if hashlib.sha256(text.encode("utf-8")).hexdigest()[:24] != ref:
            return None
        snapshot = json.loads(text)
        provider_request = snapshot["provider_request"]
        if (
            provider_request.get("evidence_level") != "physical_capture"
            or provider_request.get("execution_qualified") is not False
        ):
            return None
        wire = provider_request["final_physical_request"]
        canonical = json.dumps(wire, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != provider_request["final_physical_wire_hash"]:
            return None
        if provider_request.get("role") != getattr(request, "role", None):
            return None
        if snapshot.get("call_id") != context.get("call_id"):
            return None
        if provider_request["physical_attempt"]["provider_request_id"] != context["physical_request_capture"].get(
            "provider_request_id"
        ):
            return None
        audit = provider_request["final_request_context_audit"]
        if audit.get("audit_scope") != "provider_native_wire":
            return None
        resolved: dict[str, Any] = copy.deepcopy(audit)
        if isinstance(resolved.get("final_request_evidence_coverage"), dict):
            resolved["final_request_evidence_coverage"]["context_snapshot_ref"] = ref
        return resolved
    except (OSError, RuntimeError, TypeError, ValueError, KeyError):
        return None


def _native_text_parts(content: object) -> list[str]:
    """Return literal native text blocks, excluding tools, images and metadata."""
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
    return []


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous native feedback JSON")
        result[key] = value
    return result


def _engineering_native_coverage(
    body: Mapping[str, Any], *, protocol: str, goal: str, feedback_expected: bool
) -> tuple[bool, str]:
    """Observe goal text and correlated tool feedback in the actual native body.

    This is content presence, not effect authority or semantic quality. Opaque,
    unpaired or ambiguous results cannot prove that failure feedback is absent.
    """
    messages = body.get("messages")
    if protocol not in {"openai_chat_completions", "anthropic_messages"} or not isinstance(messages, list):
        return False, "unknown"
    goal_present = False
    pending: dict[str, str | None] = {}
    seen: set[str] = set()
    uncertain = False
    failed = False
    for message in messages:
        if not isinstance(message, dict):
            uncertain = True
            continue
        role = message.get("role")
        content = message.get("content")
        if role == "user" and goal:
            goal_present = goal_present or any(goal in part for part in _native_text_parts(content))
        calls: list[tuple[Any, Any]] = []
        results: list[tuple[Any, Any, bool]] = []
        if protocol == "openai_chat_completions":
            if role == "assistant":
                tool_calls = message.get("tool_calls", [])
                if not isinstance(tool_calls, list):
                    uncertain = True
                    tool_calls = []
                for call in tool_calls:
                    if isinstance(call, dict) and isinstance(call.get("function"), dict):
                        calls.append((call.get("id"), call["function"].get("name")))
                    else:
                        uncertain = True
            elif role == "tool":
                results.append((message.get("tool_call_id"), content, False))
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    uncertain = True
                elif block.get("type") == "tool_use":
                    if role == "assistant":
                        calls.append((block.get("id"), block.get("name")))
                    else:
                        uncertain = True
                elif block.get("type") == "tool_result":
                    if "is_error" in block and type(block["is_error"]) is not bool:
                        uncertain = True
                    if role == "user":
                        results.append((block.get("tool_use_id"), block.get("content"), block.get("is_error") is True))
                    else:
                        uncertain = True
        for call_id, tool in calls:
            if not isinstance(call_id, str) or not call_id or not isinstance(tool, str) or not tool:
                uncertain = True
            elif call_id in seen:
                uncertain = True
                pending[call_id] = None
            else:
                seen.add(call_id)
                pending[call_id] = tool
        for call_id, result_content, native_error in results:
            tool = pending.pop(call_id, None) if isinstance(call_id, str) else None
            try:
                observation = json.loads(
                    "".join(_native_text_parts(result_content)), object_pairs_hook=_unique_json_object
                )
            except (ValueError, TypeError):
                uncertain = True
                continue
            if (
                tool is None
                or not isinstance(observation, dict)
                or observation.get("call_id") != call_id
                or observation.get("tool") != tool
                or not isinstance(observation.get("content"), str)
                or type(observation.get("ok")) is not bool
            ):
                uncertain = True
            elif observation["ok"] is False:
                failed = True
            elif native_error:
                uncertain = True
    if failed:
        return goal_present, "present"
    return goal_present, "unknown" if uncertain or pending or feedback_expected else "not_applicable"


class NativeRequestCapturePort:
    """Persist the actual native request without minting a domain qualification.

    Existing Factory/session dispatch ports remain the authority for governed
    calls. This port observes ordinary transport attempts and retains the same
    immutable snapshot surface; it grants no effect or budget permissions.
    """

    def __init__(
        self,
        *,
        request: AIRequest,
        model_spec: ModelSpec,
        trace_id: str,
        messages: list[Any],
        provider_request: dict[str, Any],
        persist: Callable[..., str],
        workspace: str | None,
        prepared_surface: Mapping[str, Any] | None = None,
        native_request_observer: NativeRequestObserver | None = None,
    ) -> None:
        self._request = request
        self._model_spec = model_spec
        self._trace_id = trace_id
        self._messages = copy.deepcopy(messages)
        self._provider_request = copy.deepcopy(provider_request)
        semantic = request.context.get("provider_request_semantic_audit")
        if isinstance(semantic, dict):
            self._provider_request = {**copy.deepcopy(semantic), **self._provider_request}
            if isinstance(semantic.get("final_request_context_audit"), dict):
                self._provider_request["final_request_context_audit"] = copy.deepcopy(
                    semantic["final_request_context_audit"]
                )
        self._persist = persist
        self._workspace = workspace
        semantic_surface = request.context.get("provider_request_semantic_surface")
        surface = semantic_surface if isinstance(semantic_surface, dict) else prepared_surface
        self._prepared_surface = copy.deepcopy(dict(surface)) if surface is not None else None
        self._lock = threading.Lock()
        self._attempts = 0
        self._capture_id = uuid.uuid4().hex
        self._observer = native_request_observer
        self._final: tuple[str, dict[str, Any]] | None = None

    def _capture(self, wire_request: Mapping[str, Any]) -> tuple[Mapping[str, Any], dict[str, Any]]:
        frozen = _deep_freeze_provider_value(wire_request)
        wire = redact_provider_transport(frozen)
        canonical = json.dumps(wire, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        wire_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        body = wire.get("body")
        if not isinstance(body, dict):
            raise RuntimeError("native_request_capture_body_required")
        opaque_sdk = wire.get("transport", {}).get("kind") == "openai-python-sdk"
        if opaque_sdk:
            protocol = ""
        elif "input" in body:
            protocol = "openai_responses"
        elif "system" in body or self._model_spec.provider_type == "anthropic_compat":
            protocol = "anthropic_messages"
        elif isinstance(body.get("messages"), list):
            protocol = "openai_chat_completions"
        else:
            protocol = ""
        audit = copy.deepcopy(self._provider_request.get("final_request_context_audit") or {})
        if isinstance(self._request.context.get("visual_working_set"), dict):
            audit["visual_working_set"] = copy.deepcopy(self._request.context["visual_working_set"])
        if protocol:
            audit.update(
                provider_native_request_metrics(
                    body=body,
                    native_protocol=protocol,
                    context_window_tokens=int(self._model_spec.max_context_tokens or 0),
                )
            )
        else:
            audit["audit_scope"] = "provider_native_wire_unrecognized"
            audit["final_request_token_estimate"] = len(json.dumps(body, ensure_ascii=False)) // 4
        source_messages = self._request.context.get("provider_request_semantic_messages")
        source_messages = source_messages if isinstance(source_messages, list) else self._messages
        if protocol == "anthropic_messages":
            expected_messages, expected_system = build_anthropic_native_messages(source_messages)
            projection_matches = body.get("messages") == expected_messages and body.get("system") == expected_system
        elif protocol == "openai_chat_completions":
            projection_matches = body.get("messages") == build_openai_native_messages(source_messages)
        else:
            projection_matches = False
        surface = self._prepared_surface or self._provider_request
        expected_tools = surface.get("tools") or []
        expected_choice = surface.get("tool_choice")
        if protocol == "anthropic_messages":
            expected_tools = convert_tools_to_anthropic(expected_tools)
            expected_choice = convert_tool_choice_to_anthropic(expected_choice)
        surface_matches = (
            body.get("tools", []) == expected_tools
            and body.get("tool_choice") == expected_choice
            and body.get("response_format") == surface.get("response_format")
        )
        evidence_coverage = audit.get("final_request_evidence_coverage")
        if isinstance(evidence_coverage, dict):
            offered = []
            for tool in body.get("tools", []) or []:
                if isinstance(tool, dict):
                    function = tool.get("function")
                    name = function.get("name") if isinstance(function, dict) else tool.get("name")
                    if isinstance(name, str) and name:
                        offered.append(name)
            required = evidence_coverage.get("required_tools") or []
            missing = [name for name in required if name not in offered]
            evidence_coverage["available_tools"] = offered
            evidence_coverage["missing_required_tools"] = missing
            if missing:
                evidence_coverage["pass"] = False
        projection_matches = projection_matches and surface_matches
        audit["native_surface_projection_matched"] = surface_matches
        audit["coverage_verification"] = (
            "native_message_projection_matched" if projection_matches else "unverified_native_projection_changed"
        )
        if not projection_matches:
            audit["semantic_projection_coverage"] = copy.deepcopy(audit.get("coverage") or {})
            audit["coverage"] = dict.fromkeys(audit.get("coverage", {}), False)
            evidence_coverage = audit.get("final_request_evidence_coverage")
            if isinstance(evidence_coverage, dict):
                audit["semantic_projection_evidence_coverage"] = copy.deepcopy(evidence_coverage)
                evidence_coverage["pass"] = False
                evidence_coverage["native_projection_coverage_unverified"] = True
        if self._request.context.get("engine_version") == "engineering_v2":
            goal = str(self._request.input or "").strip()
            goal_present, failure_feedback = _engineering_native_coverage(
                body,
                protocol=protocol,
                goal=goal,
                feedback_expected=bool(self._request.context.get("failure_feedback")),
            )
            messages = body.get("messages") if isinstance(body.get("messages"), list) else body.get("input")
            messages = messages if isinstance(messages, list) else []
            system = body.get("system") or next(
                (item.get("content") for item in messages if isinstance(item, dict) and item.get("role") == "system"),
                None,
            )
            audit["coverage"] = {
                "original_goal": "present" if goal_present else "unknown",
                "role_instruction": "present" if protocol and system else "unknown",
                "target_context": "unknown" if self._request.context.get("session_id") else "not_applicable",
                "failure_feedback": failure_feedback,
                "pm_contract": "not_applicable",
                "chief_engineer_blueprint": "not_applicable",
            }
            audit["coverage_scope"] = "native_text_presence_only"
            # New engineering observations do not inherit old contract-admission
            # quality warnings or manufacture a new coverage verdict.
            audit.pop("context_quality", None)
        generation = audit.get("generation_budget")
        if isinstance(generation, dict) and generation and protocol:
            audit["generation_budget_matches_wire"] = type(audit.get("output_token_limit")) is int and audit[
                "output_token_limit"
            ] == generation.get("effective_output_tokens")
        audit["final_physical_wire_hash"] = wire_hash
        if isinstance(audit.get("final_request_evidence_coverage"), dict):
            audit["final_request_evidence_coverage"]["context_snapshot_ref"] = ""
        with self._lock:
            self._attempts += 1
            attempt = {
                "provider_request_id": uuid.uuid4().hex,
                "attempt_number": self._attempts,
                "status": "prepared",
                "execution_qualified": False,
            }
            provider_request = copy.deepcopy(self._provider_request)
            provider_request.update(
                evidence_level="provider_call_observation" if opaque_sdk else "physical_capture",
                execution_qualified=False,
                final_physical_request=wire,
                final_physical_wire_hash=wire_hash,
                native_protocol=protocol or None,
                final_request_context_audit=audit,
                physical_attempt=attempt,
            )
            if opaque_sdk:
                provider_request["observed_provider_call"] = provider_request.pop("final_physical_request")
                provider_request["observed_provider_call_hash"] = provider_request.pop("final_physical_wire_hash")
                provider_request["final_request_context_audit"]["audit_scope"] = "provider_call_observation"
                provider_request["final_request_context_audit"]["observed_provider_call_hash"] = provider_request[
                    "final_request_context_audit"
                ].pop("final_physical_wire_hash")
            observation = self._publish(provider_request)
        self._notify(observation)
        if audit.get("generation_budget_matches_wire") is False:
            error = RuntimeError("output_budget_wire_mismatch")
            self._finish(provider_request, status="failed", error=error)
            raise error
        return frozen, provider_request

    @staticmethod
    def _usage_evidence(result: Any) -> dict[str, Any]:
        """Retain reported/estimated response usage without inventing zero usage."""
        raw_usage: Any = None
        usage: Any = result.get("usage") if isinstance(result, Mapping) else getattr(result, "usage", None)
        for payload in (
            result if isinstance(result, Mapping) else getattr(result, "data", None),
            getattr(result, "raw", None),
        ):
            if isinstance(payload, Mapping) and isinstance(payload.get("usage"), Mapping):
                raw_usage = payload["usage"]
                break
        if isinstance(usage, Mapping):
            values = dict(usage)
        else:
            serialize = getattr(usage, "to_dict", None)
            try:
                values = serialize() if callable(serialize) else {}
            except (RuntimeError, TypeError, ValueError):
                values = {}
        if not isinstance(values, dict):
            values = {}
        token_keys = (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cached_tokens",
            "input_tokens",
            "output_tokens",
        )
        known = {
            key: value
            for key, value in values.items()
            if key in token_keys and isinstance(value, int) and not isinstance(value, bool) and value >= 0
        }
        reported = {
            key: value
            for key, value in (raw_usage.items() if isinstance(raw_usage, Mapping) else ())
            if key in token_keys and isinstance(value, int) and not isinstance(value, bool) and value >= 0
        }
        estimated = values.get("estimated") is True
        # Usage() is a convenience default, not proof that an unreported call
        # consumed zero tokens. Explicit raw zero-valued usage remains reported.
        available = bool(reported) or estimated or any(known.values())
        return {
            "usage": {**known, "estimated": estimated} if available else None,
            "usage_status": "estimated" if estimated else "reported" if available else "unknown",
            "usage_scope": "dispatch_response",
            "raw_provider_usage": reported or None,
        }

    def _observation(self, provider_request: dict[str, Any], ref: str | None) -> dict[str, Any]:
        attempt = provider_request["physical_attempt"]
        audit = provider_request["final_request_context_audit"]
        metric_keys = (
            "visual_working_set",
            "audit_scope",
            "native_protocol",
            "generation_budget",
            "output_token_limit",
            "generation_budget_matches_wire",
            "message_count",
            "message_token_estimate",
            "tool_schema_count",
            "tool_schema_token_estimate",
            "response_format_token_estimate",
            "request_control_token_estimate",
            "final_request_token_estimate",
            "context_window_tokens",
            "context_window_utilization",
            "image_count",
            "image_bytes",
            "image_sha256",
            "visual_token_estimate",
            "visual_token_status",
            "serialized_message_token_estimate",
            "serialized_final_request_token_estimate",
            "coverage",
        )
        usage = provider_request.get("usage_observation") or self._usage_evidence(None)
        return {
            "schema_version": "llm.native_request_observation.v1",
            "phase": "prepared" if attempt["status"] == "prepared" else "terminal",
            "status": attempt["status"],
            "snapshot_status": "available" if ref else "unavailable",
            "context_snapshot_ref": ref,
            "evidence_level": provider_request["evidence_level"],
            "provider_request_id": attempt["provider_request_id"],
            "capture_id": self._capture_id,
            "dispatch_attempt_number": attempt["attempt_number"],
            "dispatch_attempt_count": attempt["attempt_number"],
            "dispatch_count_scope": "capture_port",
            # A dispatch may redirect or hide SDK retries, or may be cancelled
            # before transport opens. Neither snapshots nor dispatches count sends.
            "physical_send_count": None,
            "physical_send_count_status": "unknown",
            "role": self._request.role,
            "provider_id": self._model_spec.provider_id,
            "provider_type": self._model_spec.provider_type,
            "model": self._model_spec.model,
            "audit": {key: copy.deepcopy(audit[key]) for key in metric_keys if key in audit},
            **copy.deepcopy(usage),
        }

    def _notify(self, observation: dict[str, Any]) -> None:
        """Audit callbacks cannot grant authority or trigger provider replay."""
        self._request.context["native_request_observation"] = copy.deepcopy(observation)
        if self._observer is None:
            return
        identity = (self._request.role, self._request.provider_id, self._request.model)
        protected_keys = (
            "context_snapshot_ref",
            "contextSnapshotRef",
            "physical_request_capture",
            "final_request_context_audit",
            "context_snapshot_degraded",
            "workspace",
            "session_id",
            "engine_version",
            "turn_seq",
            "call_id",
        )
        protected = {
            key: copy.deepcopy(self._request.context[key]) for key in protected_keys if key in self._request.context
        }
        try:
            self._observer(self._request, copy.deepcopy(observation))
        except Exception as exc:  # noqa: BLE001 - non-authoritative observer isolation.
            diagnostic = {
                "code": "native_request_observer_failed",
                "phase": observation["phase"],
                "error_type": type(exc).__name__,
            }
            errors = self._request.context.get("native_request_observer_errors")
            errors = list(errors) if isinstance(errors, list) else []
            self._request.context["native_request_observer_errors"] = [*errors[-7:], diagnostic]
            logger.warning(
                "Native request observer failed phase=%s error_type=%s", observation["phase"], type(exc).__name__
            )
        finally:
            # The observer consumes host identity and saved evidence; it cannot
            # replace them while the provider call is still in flight.
            self._request.role, self._request.provider_id, self._request.model = identity
            for key in protected_keys:
                if key in protected:
                    self._request.context[key] = protected[key]
                else:
                    self._request.context.pop(key, None)
            self._request.context["native_request_observation"] = copy.deepcopy(observation)

    def _publish(self, provider_request: dict[str, Any]) -> dict[str, Any]:
        identity_keys = ("workspace", "session_id", "engine_version", "turn_seq", "call_id")
        provider_request["request_identity"] = {
            key: value
            for key in identity_keys
            if isinstance((value := self._request.context.get(key)), (str, int)) and not isinstance(value, bool)
        }
        try:
            ref = validate_context_hash(
                self._persist(
                    workspace=self._workspace,
                    messages=self._messages,
                    trace_id=self._trace_id,
                    call_id=self._request.context.get("call_id"),
                    provider_request=provider_request,
                )
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise RuntimeError("native_request_capture_persist_failed") from exc
        audit = provider_request["final_request_context_audit"]
        live_audit = copy.deepcopy(audit)
        if isinstance(live_audit.get("final_request_evidence_coverage"), dict):
            live_audit["final_request_evidence_coverage"]["context_snapshot_ref"] = ref
        self._request.context["context_snapshot_ref"] = ref
        self._request.context["final_request_context_audit"] = live_audit
        self._request.context["physical_request_capture"] = {
            **provider_request["physical_attempt"],
            "context_snapshot_ref": ref,
            "snapshot_status": "available",
        }
        self._final = (ref, copy.deepcopy(live_audit))
        return self._observation(provider_request, ref)

    def _finish(
        self, provider_request: dict[str, Any], *, status: str, error: BaseException | None = None, result: Any = None
    ) -> None:
        budget = get_provider_invocation_budget()
        stop_reason = budget.stop_reason if budget is not None else ""
        if stop_reason:
            provider_request["physical_attempt"]["stop_reason"] = stop_reason
            if status == "failed":
                if stop_reason == "caller_cancelled":
                    status = "cancelled"
                elif stop_reason == "deadline":
                    status = "timed_out"
            elif status == "completed":
                provider_request["physical_attempt"]["caller_stop_requested"] = True
        provider_request["physical_attempt"]["status"] = status
        if error is not None:
            provider_request["physical_attempt"]["error_type"] = type(error).__name__
        provider_request["usage_observation"] = self._usage_evidence(result)
        with self._lock:
            try:
                observation = self._publish(provider_request)
            except RuntimeError:
                # HTTP already happened. Audit persistence cannot request another
                # physical send or leave the preparation ref labelled as final.
                self._request.context.pop("context_snapshot_ref", None)
                self._request.context.pop("contextSnapshotRef", None)
                self._request.context["context_snapshot_degraded"] = {"code": "NATIVE_FINAL_CONTEXT_STORE_FAILED"}
                self._request.context["physical_request_capture"] = {
                    **provider_request["physical_attempt"],
                    "context_snapshot_ref": None,
                    "snapshot_status": "unavailable",
                }
                self._final = None
                observation = self._observation(provider_request, None)
                logger.warning("Native terminal snapshot unavailable status=%s", status)
        self._notify(observation)

    def final_context_evidence(self) -> tuple[str, dict[str, Any]] | None:
        with self._lock:
            return copy.deepcopy(self._final)

    def dispatch_sync(
        self, *, wire_request: Mapping[str, Any], send: Callable[[Mapping[str, Any]], _Result]
    ) -> _Result:
        frozen, provider_request = self._capture(wire_request)
        try:
            result = send(frozen)
        except BaseException as exc:
            self._finish(provider_request, status="failed", error=exc)
            raise
        self._finish(provider_request, status="completed", result=result)
        return result

    async def dispatch_async(
        self, *, wire_request: Mapping[str, Any], send: Callable[[Mapping[str, Any]], Awaitable[_Result]]
    ) -> _Result:
        frozen, provider_request = await self._prepare_async(wire_request)
        try:
            result = await send(frozen)
        except BaseException as exc:
            self._finish(
                provider_request, status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed", error=exc
            )
            raise
        self._finish(provider_request, status="completed", result=result)
        return result

    async def dispatch_blocking_async(
        self, *, wire_request: Mapping[str, Any], send: Callable[[Mapping[str, Any]], _Result]
    ) -> _Result:
        return await asyncio.to_thread(self.dispatch_sync, wire_request=wire_request, send=send)

    async def _prepare_async(self, wire_request: Mapping[str, Any]) -> tuple[Mapping[str, Any], dict[str, Any]]:
        capture = asyncio.create_task(asyncio.to_thread(self._capture, wire_request))
        try:
            return await asyncio.shield(capture)
        except asyncio.CancelledError as cancellation:
            # A worker cannot be killed by cancelling its asyncio waiter. Wait
            # for its bounded local persistence before publishing cancellation;
            # no transport is opened after cancellation during preparation.
            while not capture.done():
                try:
                    await asyncio.shield(capture)
                except asyncio.CancelledError:
                    continue
            try:
                _frozen, provider_request = capture.result()
            except Exception as exc:
                raise cancellation from exc
            self._finish(provider_request, status="cancelled", error=cancellation)
            raise

    async def dispatch_stream_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        open_stream: Callable[[Mapping[str, Any]], AsyncContextManager[_Response]],
        consume: Callable[[_Response], AsyncIterator[_Result]],
    ) -> AsyncIterator[_Result]:
        frozen, provider_request = await self._prepare_async(wire_request)
        usage_result: Any = None
        try:
            async with open_stream(frozen) as response:
                stream = consume(response)
                try:
                    async for item in stream:
                        if isinstance(item, Mapping) and isinstance(item.get("usage"), Mapping):
                            usage_result = item
                        yield item
                finally:
                    close = getattr(stream, "aclose", None)
                    if close is not None:
                        await close()
        except BaseException as exc:
            self._finish(
                provider_request,
                status="cancelled" if isinstance(exc, (asyncio.CancelledError, GeneratorExit)) else "failed",
                error=exc,
            )
            raise
        self._finish(provider_request, status="completed", result=usage_result)
