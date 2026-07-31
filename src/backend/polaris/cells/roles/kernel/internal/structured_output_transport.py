"""Provider-tool transport for strict role results.

The reserved result tool is a provider protocol primitive.  Calls to it are
normalized into JSON content before TransactionKernel sees a tool call, so it
cannot enter authorization, Tool Lifecycle, effect receipts, or mutation ports.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from polaris.cells.roles.kernel.public.structured_output_contracts import (
    STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY,
    RoleStructuredOutputContractV1,
)

STRUCTURED_OUTPUT_TOOL_NAME = "submit_structured_role_output"
STRUCTURED_OUTPUT_TRANSPORT_SCHEMA = "roles.kernel.structured_output_transport.v1"
_STRUCTURED_OUTPUT_METADATA_KEY = "structured_output_transport"
_STRUCTURED_OUTPUT_DESCRIPTION_SUFFIX = (
    "Call this result-submission tool exactly once. It records no side effect and is not an executable workspace tool."
)


class _ValidatedStructuredOutputStreamEvent(dict[str, Any]):
    """In-process provenance minted only after reserved-tool validation."""


def _without_untrusted_transport_metadata(event: Mapping[str, Any]) -> dict[str, Any]:
    """Strip Provider-controlled evidence reserved for the internal projector."""

    sanitized = dict(event)
    raw_metadata = event.get("metadata")
    if isinstance(raw_metadata, Mapping):
        metadata = dict(raw_metadata)
        metadata.pop(_STRUCTURED_OUTPUT_METADATA_KEY, None)
        sanitized["metadata"] = metadata
    return sanitized


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("structured_output_payload_must_be_json_object") from exc


@dataclass(frozen=True, slots=True)
class StructuredOutputTransportPlan:
    contract: RoleStructuredOutputContractV1
    tool_definition: dict[str, Any]
    tool_choice: dict[str, Any]
    audit: dict[str, Any]


def resolve_structured_output_transport(context_override: Any) -> StructuredOutputTransportPlan | None:
    """Resolve the typed contract projected by roles.runtime, if present."""

    if not isinstance(context_override, dict):
        return None
    raw_contract = context_override.get(STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY)
    if raw_contract is None:
        return None
    if not isinstance(raw_contract, Mapping):
        raise TypeError("structured_output_contract_context_must_be_mapping")
    contract = RoleStructuredOutputContractV1.from_context_projection(raw_contract)
    tool_definition = {
        "type": "function",
        "function": {
            "name": STRUCTURED_OUTPUT_TOOL_NAME,
            "description": (f"{contract.description} {_STRUCTURED_OUTPUT_DESCRIPTION_SUFFIX}"),
            "parameters": dict(contract.json_schema),
            "strict": True,
        },
    }
    tool_choice = {
        "type": "function",
        "function": {"name": STRUCTURED_OUTPUT_TOOL_NAME},
    }
    audit = {
        "schema_version": STRUCTURED_OUTPUT_TRANSPORT_SCHEMA,
        "schema_name": contract.schema_name,
        "transport": contract.transport,
        "tool_name": STRUCTURED_OUTPUT_TOOL_NAME,
        "strict": True,
        "side_effect": False,
        "tool_lifecycle": False,
    }
    return StructuredOutputTransportPlan(
        contract=contract,
        tool_definition=tool_definition,
        tool_choice=tool_choice,
        audit=audit,
    )


def require_exact_structured_output_tool_surface(
    tool_definitions: list[dict[str, Any]],
) -> bool:
    """Recognize only the canonical singleton result-protocol surface.

    Returns ``False`` when the reserved tool is absent. If the reserved name is
    present, every envelope invariant must match the transport emitted by
    :func:`resolve_structured_output_transport`; otherwise the request fails
    closed before prompt synthesis. The caller-owned property schema is
    independently bound and compared again by final Provider qualification.
    """

    reserved_definitions: list[Mapping[str, Any]] = []
    for raw_definition in tool_definitions:
        if not isinstance(raw_definition, Mapping):
            continue
        raw_function = raw_definition.get("function")
        if isinstance(raw_function, Mapping):
            name = str(raw_function.get("name") or "").strip()
        else:
            name = str(raw_definition.get("name") or "").strip()
        if name == STRUCTURED_OUTPUT_TOOL_NAME:
            reserved_definitions.append(raw_definition)

    if not reserved_definitions:
        return False
    if len(tool_definitions) != 1 or len(reserved_definitions) != 1:
        raise ValueError("structured_output_tool_surface_must_be_exact_singleton")

    definition = reserved_definitions[0]
    if set(definition) != {"type", "function"} or definition.get("type") != "function":
        raise ValueError("structured_output_tool_definition_envelope_malformed")
    function = definition.get("function")
    if not isinstance(function, Mapping):
        raise ValueError("structured_output_tool_function_must_be_mapping")
    if set(function) != {"name", "description", "parameters", "strict"}:
        raise ValueError("structured_output_tool_function_envelope_malformed")
    if function.get("name") != STRUCTURED_OUTPUT_TOOL_NAME or function.get("strict") is not True:
        raise ValueError("structured_output_tool_function_identity_malformed")
    description = str(function.get("description") or "").strip()
    if not description or not description.endswith(_STRUCTURED_OUTPUT_DESCRIPTION_SUFFIX):
        raise ValueError("structured_output_tool_description_malformed")
    parameters = function.get("parameters")
    if not isinstance(parameters, Mapping) or parameters.get("type") != "object":
        raise ValueError("structured_output_tool_parameters_must_be_object_schema")
    properties = parameters.get("properties", {})
    required = parameters.get("required", [])
    if not isinstance(properties, Mapping):
        raise ValueError("structured_output_tool_properties_must_be_mapping")
    if (
        not isinstance(required, list)
        or any(not isinstance(item, str) or not item.strip() for item in required)
        or len(set(required)) != len(required)
        or bool(set(required).difference(properties))
    ):
        raise ValueError("structured_output_tool_required_fields_malformed")
    Draft202012Validator.check_schema(dict(parameters))
    return True


def _tool_call_name(call: Mapping[str, Any]) -> str:
    function = call.get("function")
    if isinstance(function, Mapping):
        return str(function.get("name") or "").strip()
    return str(call.get("tool") or call.get("name") or "").strip()


def _tool_call_arguments(call: Mapping[str, Any]) -> dict[str, Any]:
    function = call.get("function")
    raw_arguments = function.get("arguments") if isinstance(function, Mapping) else None
    if raw_arguments is None:
        raw_arguments = call.get("args", call.get("arguments"))
    if isinstance(raw_arguments, str):
        try:
            raw_arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ValueError("structured_output_tool_arguments_invalid_json") from exc
    if not isinstance(raw_arguments, Mapping):
        raise ValueError("structured_output_tool_arguments_must_be_object")
    return dict(raw_arguments)


def _tool_call_id(call: Mapping[str, Any]) -> str:
    return str(call.get("call_id") or call.get("id") or "").strip()


def _transport_evidence(
    plan: StructuredOutputTransportPlan,
    *,
    payload_json: str,
    call_id: str,
) -> dict[str, Any]:
    return {
        **plan.audit,
        "call_id": call_id,
        "payload_sha256": hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
    }


def is_canonical_structured_output_stream_chunk(event: Mapping[str, Any]) -> bool:
    """Recognize one internally validated result-protocol JSON chunk.

    The protocol normalizer emits a canonical JSON chunk before the terminal
    stream event. Downstream visible-text filters must not reinterpret that
    protocol payload as patch syntax, XML, thinking text, or bracket-wrapped
    tool text. Recognition is bound to the canonical payload hash and the
    non-effect transport invariants, so an ordinary Provider text chunk cannot
    opt itself out of those filters merely by choosing a metadata key.
    """

    if type(event) is not _ValidatedStructuredOutputStreamEvent:
        return False
    if str(event.get("type") or "").strip() != "chunk":
        return False
    metadata = event.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    evidence = metadata.get(_STRUCTURED_OUTPUT_METADATA_KEY)
    if not isinstance(evidence, Mapping):
        return False
    if (
        evidence.get("schema_version") != STRUCTURED_OUTPUT_TRANSPORT_SCHEMA
        or evidence.get("transport") != "provider_tool"
        or evidence.get("tool_name") != STRUCTURED_OUTPUT_TOOL_NAME
        or evidence.get("strict") is not True
        or evidence.get("side_effect") is not False
        or evidence.get("tool_lifecycle") is not False
    ):
        return False
    content = str(event.get("content") or "")
    if not content:
        return False
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != str(evidence.get("payload_sha256") or ""):
        return False
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, Mapping)


def trusted_structured_output_stream_evidence(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return evidence only for an internally minted validated stream event."""

    if type(event) is not _ValidatedStructuredOutputStreamEvent:
        return None
    metadata = event.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    evidence = metadata.get(_STRUCTURED_OUTPUT_METADATA_KEY)
    if not isinstance(evidence, Mapping):
        return None
    if (
        evidence.get("schema_version") != STRUCTURED_OUTPUT_TRANSPORT_SCHEMA
        or evidence.get("transport") != "provider_tool"
        or evidence.get("tool_name") != STRUCTURED_OUTPUT_TOOL_NAME
        or evidence.get("strict") is not True
        or evidence.get("side_effect") is not False
        or evidence.get("tool_lifecycle") is not False
    ):
        return None
    return dict(evidence)


def _validate_payload(
    payload: Mapping[str, Any],
    plan: StructuredOutputTransportPlan,
) -> None:
    errors = sorted(
        Draft202012Validator(plan.contract.json_schema).iter_errors(dict(payload)),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if not errors:
        return
    first = errors[0]
    path = ".".join(str(part) for part in first.absolute_path) or "$"
    raise ValueError(f"structured_output_payload_schema_mismatch:{path}:{first.message}")


def validate_structured_output_stream_tool_call(
    *,
    tool_name: str,
    arguments: Any,
    plan: StructuredOutputTransportPlan | None,
) -> None:
    """Validate the reserved result call before it leaves the Provider stream.

    Stream consumers may stop iteration as soon as they receive a tool call.
    Validating only in a downstream consumer therefore lets a schema exception
    escape outside the LLM call lifecycle, leaving a physical attempt with a
    start event but no terminal call_error. Keep validation in the Provider
    stream boundary as well as in the normalizer so every invalid physical
    result is closed and remains available to bounded caller repair policy.
    """

    if plan is None or str(tool_name or "").strip() != STRUCTURED_OUTPUT_TOOL_NAME:
        return
    if not isinstance(arguments, Mapping):
        raise ValueError("structured_output_tool_arguments_must_be_object")
    _validate_payload(arguments, plan)


def normalize_structured_output_response(
    response: dict[str, Any],
    plan: StructuredOutputTransportPlan | None,
) -> dict[str, Any]:
    """Convert one reserved result-tool call into strict JSON content."""

    if plan is None:
        return response
    raw_calls = response.get("tool_calls")
    if not isinstance(raw_calls, list):
        raw_calls = response.get("native_tool_calls")
    calls = [item for item in raw_calls or [] if isinstance(item, Mapping)]
    result_calls = [item for item in calls if _tool_call_name(item) == STRUCTURED_OUTPUT_TOOL_NAME]
    if not result_calls:
        return response
    if len(result_calls) != 1:
        raise ValueError("structured_output_tool_must_be_called_exactly_once")
    result_call = result_calls[0]
    payload = _tool_call_arguments(result_call)
    _validate_payload(payload, plan)
    payload_json = _canonical_json(payload)
    evidence = _transport_evidence(
        plan,
        payload_json=payload_json,
        call_id=_tool_call_id(result_call),
    )
    normalized = dict(response)
    normalized["content"] = payload_json
    normalized["tool_calls"] = []
    normalized["native_tool_calls"] = []
    normalized["structured_output_transport"] = evidence
    metadata = dict(normalized.get("metadata") or {})
    metadata["structured_output_transport"] = evidence
    normalized["metadata"] = metadata
    return normalized


class StructuredOutputStreamNormalizer:
    """Stateful stream projector that hides the reserved protocol tool call."""

    __slots__ = ("_buffered_chunks", "_call_id", "_payload_json", "_plan")

    def __init__(self, plan: StructuredOutputTransportPlan) -> None:
        self._plan = plan
        self._buffered_chunks: list[dict[str, Any]] = []
        self._payload_json: str | None = None
        self._call_id = ""

    def project(self, event: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        sanitized_event = _without_untrusted_transport_metadata(event)
        event_type = str(sanitized_event.get("type") or "").strip()
        if event_type == "chunk":
            self._buffered_chunks.append(sanitized_event)
            return ()
        if event_type == "tool_call" and str(sanitized_event.get("tool") or "").strip() == STRUCTURED_OUTPUT_TOOL_NAME:
            if self._payload_json is not None:
                raise ValueError("structured_output_tool_must_be_called_exactly_once")
            args = sanitized_event.get("args")
            if not isinstance(args, Mapping):
                raise ValueError("structured_output_tool_arguments_must_be_object")
            _validate_payload(args, self._plan)
            self._payload_json = _canonical_json(args)
            self._call_id = str(sanitized_event.get("call_id") or "").strip()
            return ()
        if event_type != "complete":
            return (sanitized_event,)
        if self._payload_json is None:
            buffered = tuple(self._buffered_chunks)
            self._buffered_chunks.clear()
            return (*buffered, sanitized_event)
        evidence = _transport_evidence(
            self._plan,
            payload_json=self._payload_json,
            call_id=self._call_id,
        )
        metadata = dict(sanitized_event.get("metadata") or {})
        metadata[_STRUCTURED_OUTPUT_METADATA_KEY] = evidence
        complete = _ValidatedStructuredOutputStreamEvent(sanitized_event)
        complete["metadata"] = metadata
        self._buffered_chunks.clear()
        return (
            _ValidatedStructuredOutputStreamEvent(
                {
                    "type": "chunk",
                    "content": self._payload_json,
                    "metadata": {_STRUCTURED_OUTPUT_METADATA_KEY: evidence},
                }
            ),
            complete,
        )


__all__ = [
    "STRUCTURED_OUTPUT_TOOL_NAME",
    "STRUCTURED_OUTPUT_TRANSPORT_SCHEMA",
    "StructuredOutputStreamNormalizer",
    "StructuredOutputTransportPlan",
    "is_canonical_structured_output_stream_chunk",
    "normalize_structured_output_response",
    "require_exact_structured_output_tool_surface",
    "resolve_structured_output_transport",
    "trusted_structured_output_stream_evidence",
    "validate_structured_output_stream_tool_call",
]
