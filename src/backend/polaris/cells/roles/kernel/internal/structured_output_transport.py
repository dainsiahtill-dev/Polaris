"""Provider-tool transport for strict role results.

The reserved result tool is a provider protocol primitive.  Calls to it are
normalized into JSON content before TransactionKernel sees a tool call, so it
cannot enter authorization, Tool Lifecycle, effect receipts, or mutation ports.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from polaris.cells.roles.kernel.public.structured_output_contracts import (
    STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY,
    RoleStructuredOutputContractV1,
)
from polaris.cells.roles.kernel.public.turn_contracts import RawLLMResponse

STRUCTURED_OUTPUT_TOOL_NAME = "submit_structured_role_output"
STRUCTURED_OUTPUT_TRANSPORT_SCHEMA = "roles.kernel.structured_output_transport.v1"
_STRUCTURED_OUTPUT_METADATA_KEY = "structured_output_transport"
_STRUCTURED_OUTPUT_DESCRIPTION_SUFFIX = (
    "Call this result-submission tool exactly once. It records no side effect and is not an executable workspace tool."
)
_MAX_SCHEMA_CONTAINER_STRING_CHARS = 262_144
_SINGLETON_ITEM_WRAPPER_KEYS = frozenset({"item", "items"})

logger = logging.getLogger(__name__)


class _ValidatedStructuredOutputStreamEvent(dict[str, Any]):
    """In-process provenance minted only after reserved-tool validation."""


class _ValidatedStructuredOutputResponse(dict[str, Any]):
    """Non-stream response minted only after reserved-tool validation."""


class _ValidatedStructuredOutputRawResponse(RawLLMResponse):
    """Transaction response carrying proven non-executable result transport."""


def _without_untrusted_transport_metadata(event: Mapping[str, Any]) -> dict[str, Any]:
    """Strip Provider-controlled evidence reserved for the internal projector."""

    sanitized = dict(event)
    raw_metadata = event.get("metadata")
    if isinstance(raw_metadata, Mapping):
        metadata = dict(raw_metadata)
        metadata.pop(_STRUCTURED_OUTPUT_METADATA_KEY, None)
        sanitized["metadata"] = metadata
    return sanitized


def _canonical_json_value(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("structured_output_payload_must_be_json_object") from exc


def _canonical_json(value: Mapping[str, Any]) -> str:
    return _canonical_json_value(dict(value))


def _reject_duplicate_json_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous Provider JSON instead of accepting last-key-wins."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"structured_output_duplicate_json_member:{key}")
        result[key] = value
    return result


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


def validate_structured_output_content(
    content: str,
    context_override: Any,
) -> tuple[dict[str, Any], StructuredOutputTransportPlan] | None:
    """Validate canonical result content against its caller-owned contract.

    Role-level quality shapes are only defaults for free-form role output.
    A typed result protocol owns a narrower caller schema (for example a CE
    semantic-repair patch rather than a full CE portfolio), so validating that
    payload again by role would reject a result already proven by the exact
    provider-tool contract.
    """

    plan = resolve_structured_output_transport(context_override)
    if plan is None:
        return None
    try:
        payload = json.loads(content, object_pairs_hook=_reject_duplicate_json_object_pairs)
    except json.JSONDecodeError as exc:
        raise ValueError("structured_output_content_invalid_json") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("structured_output_content_must_be_object")
    return _validate_payload(payload, plan), plan


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
        # Keep provider-native envelopes lossless at this transport boundary.
        # Anthropic exposes forced-tool arguments as ``tool_use.input`` while
        # OpenAI-style calls use ``function.arguments``. Both still pass the
        # same strict JSON-object and caller-schema validation below.
        raw_arguments = call.get("args", call.get("arguments", call.get("input")))
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
    schema_normalization_policy: str = "none",
    schema_normalization_details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = {
        **plan.audit,
        "call_id": call_id,
        "payload_sha256": hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        "schema_normalization_applied": schema_normalization_policy != "none",
        "schema_normalization_policy": schema_normalization_policy,
    }
    if schema_normalization_details:
        evidence["schema_normalization_details"] = dict(schema_normalization_details)
    return evidence


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


def _trusted_structured_output_evidence(
    *,
    content: str,
    evidence: Any,
) -> dict[str, Any] | None:
    """Validate exact non-effect transport evidence against canonical content."""

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
    if not content:
        return None
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != str(evidence.get("payload_sha256") or ""):
        return None
    try:
        payload = json.loads(content, object_pairs_hook=_reject_duplicate_json_object_pairs)
    except (json.JSONDecodeError, ValueError):
        return None
    return dict(evidence) if isinstance(payload, Mapping) else None


def project_validated_structured_output_raw_response(
    response: Mapping[str, Any],
) -> RawLLMResponse | None:
    """Project an internally normalized result response into TransactionKernel.

    Ordinary provider dictionaries cannot opt out of executable-tool lifecycle:
    only the private response type minted by ``normalize_structured_output_response``
    is accepted here, and its payload hash plus non-effect invariants are checked
    again before the private ``RawLLMResponse`` subtype is created.
    """

    if type(response) is not _ValidatedStructuredOutputResponse:
        return None
    content = str(response.get("content") or "")
    evidence = _trusted_structured_output_evidence(
        content=content,
        evidence=response.get(_STRUCTURED_OUTPUT_METADATA_KEY),
    )
    metadata = response.get("metadata")
    metadata_evidence = metadata.get(_STRUCTURED_OUTPUT_METADATA_KEY) if isinstance(metadata, Mapping) else None
    if evidence is None or metadata_evidence != evidence:
        return None
    if response.get("tool_calls") or response.get("native_tool_calls"):
        return None
    thinking = response.get("thinking")
    usage = response.get("usage")
    return _ValidatedStructuredOutputRawResponse(
        content=content,
        thinking=thinking if isinstance(thinking, str) else None,
        native_tool_calls=[],
        model=str(response.get("model") or "unknown"),
        usage=dict(usage) if isinstance(usage, Mapping) else {},
        metadata={_STRUCTURED_OUTPUT_METADATA_KEY: evidence},
    )


def trusted_structured_output_response_evidence(
    response: RawLLMResponse,
) -> dict[str, Any] | None:
    """Return evidence only for the private validated transaction response."""

    if type(response) is not _ValidatedStructuredOutputRawResponse:
        return None
    content = str(response.content or "")
    evidence = response.metadata.get(_STRUCTURED_OUTPUT_METADATA_KEY)
    if response.native_tool_calls:
        return None
    return _trusted_structured_output_evidence(content=content, evidence=evidence)


def _coerce_structured_output_payload_defaults(
    payload: Mapping[str, Any],
    json_schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Fill missing/null required empty containers before strict schema validation.

    Provider structured-output frequently omits empty arrays such as CE
    ``risk_flags: []``. SCHEMA-REPAIR that re-asks the model can fail the same
    way. Coerce only schema-declared required properties whose type is
    ``array`` -> ``[]`` or semantically empty ``object`` -> ``{}``, recursively
    through declared object/array containers.  Never synthesize a missing
    object that itself has required children, nor a container with a positive
    ``minItems``/``minProperties`` contract.  That boundary lets advisory
    empty containers recover while whole semantic subcontracts remain
    fail-closed.
    """

    no_default = object()

    def required_container_default(schema: Mapping[str, Any]) -> object:
        schema_type = schema.get("type")
        if schema_type == "array":
            min_items = schema.get("minItems")
            if isinstance(min_items, int) and not isinstance(min_items, bool) and min_items > 0:
                return no_default
            return []
        if schema_type == "object":
            required_children = schema.get("required")
            min_properties = schema.get("minProperties")
            if (isinstance(required_children, (list, tuple)) and bool(required_children)) or (
                isinstance(min_properties, int) and not isinstance(min_properties, bool) and min_properties > 0
            ):
                return no_default
            return {}
        return no_default

    def coerce(value: Any, schema: Mapping[str, Any]) -> Any:
        schema_type = schema.get("type")
        if schema_type == "object" and isinstance(value, Mapping):
            result = dict(value)
            properties = schema.get("properties")
            declared = properties if isinstance(properties, Mapping) else {}
            required_raw = schema.get("required")
            required = required_raw if isinstance(required_raw, (list, tuple)) else ()
            for raw_key in required:
                if not isinstance(raw_key, str) or not raw_key.strip():
                    continue
                prop_schema = declared.get(raw_key)
                if not isinstance(prop_schema, Mapping):
                    continue
                if raw_key not in result or result[raw_key] is None:
                    default = required_container_default(prop_schema)
                    if default is not no_default:
                        result[raw_key] = default
            for raw_key, prop_schema in declared.items():
                if raw_key not in result or not isinstance(prop_schema, Mapping):
                    continue
                result[raw_key] = coerce(result[raw_key], prop_schema)
            return result
        if schema_type == "array" and isinstance(value, list):
            item_schema = schema.get("items")
            if isinstance(item_schema, Mapping):
                return [coerce(item, item_schema) for item in value]
        return value

    coerced = coerce(dict(payload), json_schema)
    return coerced if isinstance(coerced, dict) else dict(payload)


def _schema_container_type(schema: Mapping[str, Any]) -> type[dict[str, Any]] | type[list[Any]] | None:
    schema_type = schema.get("type")
    if schema_type == "object":
        return dict
    if schema_type == "array":
        return list
    return None


def _schema_type_includes(schema: Mapping[str, Any], expected: str) -> bool:
    schema_type = schema.get("type")
    if schema_type == expected:
        return True
    return isinstance(schema_type, list) and expected in schema_type


def _payload_matches_schema(value: Any, schema: Mapping[str, Any]) -> bool:
    try:
        Draft202012Validator(dict(schema)).validate(value)
    except (ValidationError, SchemaError, TypeError, ValueError):
        return False
    return True


def _unwrap_singleton_item_wrapper(value: Any) -> Any | None:
    """Return the inner payload of a one-key ``item``/``items`` wrapper, else None."""

    if not isinstance(value, Mapping):
        return None
    keys = list(value)
    if len(keys) != 1 or keys[0] not in _SINGLETON_ITEM_WRAPPER_KEYS:
        return None
    return value[keys[0]]


def _normalize_schema_proven_root_item_wrapper(
    value: Any,
    schema: Mapping[str, Any],
) -> tuple[Any, bool]:
    """Unwrap one provider-added root ``item`` envelope for a closed object.

    This recovery is deliberately root-only.  The caller schema must prove a
    closed object that does not itself declare ``item``/``items``; the payload
    must contain exactly that one wrapper; and the wrapped value must remain an
    object for the existing recursive normalizers and final strict validation.
    Nested object fields keep their original fail-closed semantics.
    """

    if not _schema_type_includes(schema, "object"):
        return value, False
    if schema.get("additionalProperties") is not False:
        return value, False
    properties = schema.get("properties")
    declared = properties if isinstance(properties, Mapping) else {}
    if any(key in declared for key in _SINGLETON_ITEM_WRAPPER_KEYS):
        return value, False
    wrapped = _unwrap_singleton_item_wrapper(value)
    if not isinstance(wrapped, Mapping):
        return value, False
    return dict(wrapped), True


def _normalize_schema_proven_singleton_item_wrapper(
    value: Any,
    schema: Mapping[str, Any],
) -> tuple[Any, bool]:
    """Unwrap provider ``{"item": ...}`` only where the caller schema is an array.

    MiniMax and some OpenAPI-strict tool runtimes serialize a JSON-Schema
    ``array`` as a singleton object keyed ``item``/``items``.  That envelope is
    protocol noise: the caller already declared ``type=array``.  Unwrap one
    layer when the reconstructed value still satisfies that exact array
    schema.  Do not unwrap object fields, invent members, or accept wrappers
    that keep extra keys.
    """

    if _schema_type_includes(schema, "array"):
        wrapped = _unwrap_singleton_item_wrapper(value)
        if wrapped is not None:
            candidate = wrapped if isinstance(wrapped, list) else [wrapped]
            inner, _inner_changed = _normalize_schema_proven_singleton_item_wrapper(candidate, schema)
            if _payload_matches_schema(inner, schema):
                return inner, True
        if isinstance(value, list):
            item_schema = schema.get("items")
            if isinstance(item_schema, Mapping):
                result_items: list[Any] = []
                changed = False
                for item in value:
                    normalized_item, item_changed = _normalize_schema_proven_singleton_item_wrapper(
                        item,
                        item_schema,
                    )
                    result_items.append(normalized_item)
                    changed = changed or item_changed
                return result_items, changed
        return value, False

    if _schema_type_includes(schema, "object") and isinstance(value, Mapping):
        properties = schema.get("properties")
        declared = properties if isinstance(properties, Mapping) else {}
        result = dict(value)
        changed = False
        for key, child_schema in declared.items():
            if key not in result or not isinstance(child_schema, Mapping):
                continue
            normalized_child, child_changed = _normalize_schema_proven_singleton_item_wrapper(
                result[key],
                child_schema,
            )
            if child_changed:
                result[key] = normalized_child
                changed = True
        return result, changed

    return value, False


def _normalize_schema_proven_map_item_chain(
    value: Any,
    schema: Mapping[str, Any],
    *,
    path: tuple[str, ...] = (),
) -> tuple[Any, bool, tuple[str, ...]]:
    """Recover a pure JSON map serialized as a recursive ``item`` chain.

    Some Anthropic-compatible strict-tool providers cannot express an object
    whose values are governed only by ``additionalProperties``.  They encode
    the map as a linked wrapper instead::

        {"item": {"TASK-1": {"item": ["INV-1"]},
                  "item": {"TASK-2": {"item": ["INV-2"]}}}}

    Flatten that envelope only when the caller schema proves a *pure* map
    (no declared properties and a schema-valued ``additionalProperties``),
    every chain node carries exactly one semantic key plus at most one
    continuation wrapper, every value independently satisfies the map value
    schema after ordinary singleton-array normalization, no key repeats, and
    the complete reconstructed map validates.  Any ambiguity remains
    fail-closed under the original payload.
    """

    if _schema_type_includes(schema, "array"):
        normalized_array, array_changed = _normalize_schema_proven_singleton_item_wrapper(value, schema)
        if not isinstance(normalized_array, list):
            return value, False, ()
        item_schema = schema.get("items")
        if not isinstance(item_schema, Mapping):
            return normalized_array, array_changed, ()
        result_items: list[Any] = []
        changed = array_changed
        array_paths: list[str] = []
        for index, item in enumerate(normalized_array):
            normalized_item, item_changed, item_paths = _normalize_schema_proven_map_item_chain(
                item,
                item_schema,
                path=(*path, str(index)),
            )
            result_items.append(normalized_item)
            changed = changed or item_changed
            array_paths.extend(item_paths)
        return result_items, changed, tuple(array_paths)

    if not (_schema_type_includes(schema, "object") and isinstance(value, Mapping)):
        return value, False, ()

    properties = schema.get("properties")
    declared = properties if isinstance(properties, Mapping) else {}
    additional = schema.get("additionalProperties")

    if not declared and isinstance(additional, Mapping):
        wrapped = _unwrap_singleton_item_wrapper(value)
        if isinstance(wrapped, Mapping):
            flattened: dict[str, Any] = {}
            cursor: Mapping[str, Any] = wrapped
            valid_chain = True
            while True:
                wrapper_keys = [key for key in cursor if str(key) in _SINGLETON_ITEM_WRAPPER_KEYS]
                semantic_keys = [key for key in cursor if str(key) not in _SINGLETON_ITEM_WRAPPER_KEYS]
                if len(wrapper_keys) > 1 or len(semantic_keys) != 1:
                    valid_chain = False
                    break
                raw_semantic_key = semantic_keys[0]
                semantic_key = str(raw_semantic_key).strip()
                if not semantic_key or semantic_key in flattened:
                    valid_chain = False
                    break
                normalized_child, _child_changed, _child_paths = _normalize_schema_proven_map_item_chain(
                    cursor[raw_semantic_key],
                    additional,
                    path=(*path, semantic_key),
                )
                if not _payload_matches_schema(normalized_child, additional):
                    valid_chain = False
                    break
                flattened[semantic_key] = normalized_child
                if not wrapper_keys:
                    break
                next_cursor = cursor[wrapper_keys[0]]
                if not isinstance(next_cursor, Mapping):
                    valid_chain = False
                    break
                cursor = next_cursor
            if valid_chain and flattened and _payload_matches_schema(flattened, schema):
                normalized_path = ".".join(path) or "$"
                return flattened, True, (normalized_path,)

    result = dict(value)
    changed = False
    object_paths: list[str] = []
    for raw_key, child_value in tuple(result.items()):
        child_schema = declared.get(raw_key)
        if not isinstance(child_schema, Mapping) and isinstance(additional, Mapping):
            child_schema = additional
        if not isinstance(child_schema, Mapping):
            continue
        normalized_child, child_changed, child_paths = _normalize_schema_proven_map_item_chain(
            child_value,
            child_schema,
            path=(*path, str(raw_key)),
        )
        if child_changed:
            result[raw_key] = normalized_child
            changed = True
        object_paths.extend(child_paths)
    return result, changed, tuple(object_paths)


def _normalize_schema_proven_self_named_empty_wrapper(
    value: Any,
    schema: Mapping[str, Any],
    *,
    field_name: str | None = None,
) -> tuple[Any, bool]:
    """Drop only an empty scalar wrapper that repeats its declared field name.

    Some Provider tool transports encode an empty object field as
    ``{"field": {"field": ""}}``.  When the outer schema proves that ``field``
    is an empty-allowed object/array, the repeated key carries no semantic
    content and can be reduced to the corresponding empty container.  A
    non-empty value, siblings, or a non-empty schema remains untouched and is
    validated fail-closed.
    """

    expected_type = _schema_container_type(schema)
    if (
        field_name
        and expected_type is not None
        and isinstance(value, Mapping)
        and list(value) == [field_name]
    ):
        wrapped = value[field_name]
        scalar_empty = wrapped is None or (isinstance(wrapped, str) and not wrapped.strip())
        empty_value: dict[str, Any] | list[Any] = {} if expected_type is dict else []
        if scalar_empty and _payload_matches_schema(empty_value, schema):
            return empty_value, True

    schema_type = schema.get("type")
    if schema_type == "object" and isinstance(value, Mapping):
        result = dict(value)
        properties = schema.get("properties")
        declared = properties if isinstance(properties, Mapping) else {}
        additional = schema.get("additionalProperties")
        changed = False
        for raw_key, child_value in tuple(result.items()):
            child_schema = declared.get(raw_key)
            if not isinstance(child_schema, Mapping) and isinstance(additional, Mapping):
                child_schema = additional
            if not isinstance(child_schema, Mapping):
                continue
            normalized_child, child_changed = _normalize_schema_proven_self_named_empty_wrapper(
                child_value,
                child_schema,
                field_name=str(raw_key),
            )
            if child_changed:
                result[raw_key] = normalized_child
                changed = True
        return result, changed

    if schema_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if not isinstance(item_schema, Mapping):
            return value, False
        result_items: list[Any] = []
        changed = False
        for item in value:
            normalized_item, item_changed = _normalize_schema_proven_self_named_empty_wrapper(
                item,
                item_schema,
            )
            result_items.append(normalized_item)
            changed = changed or item_changed
        return result_items, changed

    return value, False


def _bounded_json_container_candidates(raw_value: str) -> tuple[str, ...]:
    """Return strict, bounded JSON candidates for one wrongly stringified container.

    Some Provider tool implementations add one extra string-escaping layer to
    nested JSON.  Two invalid escapes recur in that envelope: ``\\'`` (JSON
    does not escape apostrophes) and ``\\\\\"`` (a quote escaped twice).  The
    second candidate removes exactly that one accidental layer.  No general
    JSON5/regex recovery is attempted.
    """

    stripped = raw_value.strip()
    if not stripped or len(stripped) > _MAX_SCHEMA_CONTAINER_STRING_CHARS:
        return ()
    repaired = stripped.replace("\\'", "'").replace('\\\\"', '\\"')
    if repaired == stripped:
        return (stripped,)
    return (stripped, repaired)


def _normalize_schema_proven_json_containers(
    payload: Mapping[str, Any],
    json_schema: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    """Decode only JSON-string containers that the caller schema proves.

    Besides ordinary ``{"field": "{...}"}``, recover one common Provider
    envelope defect where all remaining root members are serialized into the
    first object field, for example ``construction_plan='{"task_plans": ...},
    "risk_flags": []}'``.  Root-fragment recovery is accepted only when the
    reconstructed object uses declared schema properties, preserves every
    already-structured sibling exactly, and later passes the full caller JSON
    schema.  This is protocol normalization, not semantic repair.
    """

    properties = json_schema.get("properties")
    if not isinstance(properties, Mapping):
        return dict(payload), "none"
    result = dict(payload)
    policies: list[str] = []

    for key, property_schema_raw in properties.items():
        if not isinstance(key, str) or not isinstance(property_schema_raw, Mapping):
            continue
        expected_type = _schema_container_type(property_schema_raw)
        raw_value = result.get(key)
        if expected_type is None or not isinstance(raw_value, str):
            continue
        candidates = _bounded_json_container_candidates(raw_value)
        if not candidates:
            continue

        # First recover a Provider envelope that serialized the remaining root
        # members into this first object field.  Prefixing the declared key is
        # sufficient to reconstruct the original root object; full validation
        # below remains fail-closed.
        root_recovered = False
        if expected_type is dict:
            for candidate in candidates:
                if not (candidate.startswith("{") and candidate.endswith("}")):
                    continue
                try:
                    decoded_root = json.loads(
                        "{" + json.dumps(key) + ":" + candidate,
                        object_pairs_hook=_reject_duplicate_json_object_pairs,
                    )
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(decoded_root, Mapping) or not isinstance(decoded_root.get(key), dict):
                    continue
                if any(str(candidate_key) not in properties for candidate_key in decoded_root):
                    continue
                if any(
                    sibling_key != key
                    and (
                        sibling_key not in decoded_root
                        or _canonical_json_value(decoded_root[sibling_key]) != _canonical_json_value(sibling_value)
                    )
                    for sibling_key, sibling_value in result.items()
                ):
                    continue
                result = dict(decoded_root)
                policies.append("schema_proven_root_fragment_v1")
                root_recovered = True
                break
            if root_recovered:
                break

        for candidate in candidates:
            try:
                decoded_value = json.loads(
                    candidate,
                    object_pairs_hook=_reject_duplicate_json_object_pairs,
                )
            except (json.JSONDecodeError, ValueError):
                continue
            if type(decoded_value) is expected_type:
                result[key] = decoded_value
                policies.append("schema_proven_json_container_v1")
                break

    return result, "+".join(policies) if policies else "none"


def _declared_object_property_paths(
    schema: Mapping[str, Any],
    property_name: str,
    *,
    path: tuple[str, ...] = (),
) -> tuple[tuple[str, ...], ...]:
    """Return descendant object-property paths with one exact declared name.

    Array item paths are intentionally excluded: a displaced root member does
    not carry an authoritative array index, so relocating it into an array
    would invent structure rather than normalize a Provider envelope.
    """

    if not _schema_type_includes(schema, "object"):
        return ()
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return ()
    matches: list[tuple[str, ...]] = []
    for raw_key, child_schema in properties.items():
        if not isinstance(raw_key, str) or not isinstance(child_schema, Mapping):
            continue
        child_path = (*path, raw_key)
        if path and raw_key == property_name:
            matches.append(child_path)
        if _schema_type_includes(child_schema, "object"):
            matches.extend(
                _declared_object_property_paths(
                    child_schema,
                    property_name,
                    path=child_path,
                )
            )
    return tuple(matches)


def _insert_displaced_object_member(
    payload: dict[str, Any],
    path: tuple[str, ...],
    value: Any,
) -> bool:
    """Insert one displaced member without overwriting an existing value."""

    if len(path) < 2:
        return False
    cursor: dict[str, Any] = payload
    for key in path[:-1]:
        current = cursor.get(key)
        if not isinstance(current, Mapping):
            return False
        if not isinstance(current, dict):
            current = dict(current)
            cursor[key] = current
        cursor = current
    leaf = path[-1]
    if leaf in cursor:
        return _canonical_json_value(cursor[leaf]) == _canonical_json_value(value)
    cursor[leaf] = deepcopy(value)
    return True


def _declared_descendant_values(
    value: Any,
    schema: Mapping[str, Any],
    property_name: str,
    *,
    path: tuple[str, ...] = (),
) -> tuple[Any, ...]:
    """Collect present values for one schema-declared descendant property."""

    if _schema_type_includes(schema, "array") and isinstance(value, list):
        item_schema = schema.get("items")
        if not isinstance(item_schema, Mapping):
            return ()
        matches: list[Any] = []
        for index, item in enumerate(value):
            matches.extend(
                _declared_descendant_values(
                    item,
                    item_schema,
                    property_name,
                    path=(*path, str(index)),
                )
            )
        return tuple(matches)

    if not (_schema_type_includes(schema, "object") and isinstance(value, Mapping)):
        return ()
    properties = schema.get("properties")
    declared = properties if isinstance(properties, Mapping) else {}
    additional = schema.get("additionalProperties")
    matches = []
    for raw_key, child_value in value.items():
        child_schema = declared.get(raw_key)
        if not isinstance(child_schema, Mapping) and isinstance(additional, Mapping):
            child_schema = additional
        if not isinstance(child_schema, Mapping):
            continue
        key = str(raw_key)
        if path and key == property_name:
            matches.append(child_value)
        matches.extend(
            _declared_descendant_values(
                child_value,
                child_schema,
                property_name,
                path=(*path, key),
            )
        )
    return tuple(matches)


def _normalize_schema_proven_duplicate_root_members(
    payload: Mapping[str, Any],
    json_schema: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Drop only root members that duplicate an existing declared descendant.

    Anthropic-compatible result-tool transports can repeat a nested scalar at
    the strict root after correctly serializing the nested object/array.  A
    same-named field is transport noise only when its canonical JSON value is
    already present under a schema-declared descendant path.  Non-duplicates
    remain untouched and therefore fail closed during final schema validation.
    """

    if json_schema.get("additionalProperties") is not False:
        return dict(payload), ()
    properties = json_schema.get("properties")
    if not isinstance(properties, Mapping):
        return dict(payload), ()
    declared_root = {str(key) for key in properties}
    candidate = deepcopy(dict(payload))
    removed: list[str] = []
    for raw_key, root_value in tuple(candidate.items()):
        key = str(raw_key)
        if key in declared_root:
            continue
        descendant_values = _declared_descendant_values(candidate, json_schema, key)
        root_canonical = _canonical_json_value(root_value)
        if any(_canonical_json_value(value) == root_canonical for value in descendant_values):
            candidate.pop(raw_key, None)
            removed.append(key)
    if removed:
        logger.warning(
            "structured_output_duplicate_root_members_recovered: removed=%s",
            sorted(removed),
        )
    return candidate, tuple(sorted(removed))


def _normalize_schema_proven_displaced_root_members(
    payload: Mapping[str, Any],
    json_schema: Mapping[str, Any],
) -> tuple[dict[str, Any], bool, tuple[str, ...], bool]:
    """Recover one strict Provider result whose nested members leaked to root.

    Some Anthropic-compatible models return a complete result-tool payload but
    close a nested object too early, leaving later members at the schema-closed
    root. Recover only structure proven by the caller schema:

    * a leaked name must have exactly one declared descendant object path;
    * collisions reject recovery instead of overwriting either value;
    * every unknown root member must have one unique declared destination;
      arbitrary residual members reject recovery instead of being hidden in an
      open advisory object;
    * the full original strict schema must validate the reconstructed payload.

    No semantic field is invented or discarded. If any condition is
    ambiguous, return the original payload so normal validation fails closed.
    """

    if json_schema.get("additionalProperties") is not False:
        return dict(payload), False, (), False
    properties = json_schema.get("properties")
    if not isinstance(properties, Mapping):
        return dict(payload), False, (), False
    declared_root = {str(key) for key in properties}
    unknown_root = [str(key) for key in payload if str(key) not in declared_root]
    if not unknown_root:
        return dict(payload), False, (), False

    candidate = deepcopy(dict(payload))
    relocations: list[tuple[str, tuple[str, ...]]] = []
    for key in unknown_root:
        paths = _declared_object_property_paths(json_schema, key)
        if len(paths) == 1:
            relocations.append((key, paths[0]))
    if len(relocations) != len(unknown_root):
        return dict(payload), False, (), False

    # Parents such as ``task_plans`` must move before children such as
    # ``TASK-2`` so later inserts merge into the preserved object.
    for key, path in sorted(relocations, key=lambda item: len(item[1])):
        if key not in candidate or not _insert_displaced_object_member(candidate, path, candidate[key]):
            return dict(payload), False, (), False
        candidate.pop(key, None)

    residual = [str(key) for key in candidate if str(key) not in declared_root]
    if residual:
        return dict(payload), False, (), False

    coerced_candidate = _coerce_structured_output_payload_defaults(candidate, json_schema)
    defaults_applied = coerced_candidate != candidate
    if not _payload_matches_schema(coerced_candidate, json_schema):
        return dict(payload), False, (), False
    relocation_evidence = tuple(sorted(f"{key}->{'.'.join(path)}" for key, path in relocations))
    logger.warning(
        "structured_output_displaced_root_members_recovered: relocated=%s residual_rejected=[]",
        list(relocation_evidence),
    )
    return coerced_candidate, True, relocation_evidence, defaults_applied


def _normalize_schema_closed_object_text_noise(
    value: Any,
    schema: Mapping[str, Any],
) -> tuple[Any, bool]:
    """Remove one provider-only ``$text`` member from schema-closed objects.

    Some Anthropic-compatible structured-tool providers serialize an otherwise
    valid JSON object with an XML-style ``$text`` shadow member.  Normalize
    only that exact key, only when the caller schema explicitly closes the
    object with ``additionalProperties: false``, does not declare ``$text``,
    and the shadow value is a string.  The complete caller schema is still
    applied afterwards, so this cannot supply missing semantics or hide any
    other unknown member.
    """

    schema_type = schema.get("type")
    if schema_type == "object" and isinstance(value, Mapping):
        properties = schema.get("properties")
        declared = properties if isinstance(properties, Mapping) else {}
        result = dict(value)
        changed = False
        if (
            schema.get("additionalProperties") is False
            and "$text" not in declared
            and isinstance(result.get("$text"), str)
        ):
            result.pop("$text")
            changed = True
        for key, child_schema in declared.items():
            if key not in result or not isinstance(child_schema, Mapping):
                continue
            normalized_child, child_changed = _normalize_schema_closed_object_text_noise(
                result[key],
                child_schema,
            )
            if child_changed:
                result[key] = normalized_child
                changed = True
        return result, changed

    if schema_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if not isinstance(item_schema, Mapping):
            return value, False
        result_items: list[Any] = []
        changed = False
        for item in value:
            normalized_item, item_changed = _normalize_schema_closed_object_text_noise(item, item_schema)
            result_items.append(normalized_item)
            changed = changed or item_changed
        return result_items, changed

    return value, False


def _validate_payload_with_normalization(
    payload: Mapping[str, Any],
    plan: StructuredOutputTransportPlan,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    schema = plan.contract.json_schema
    if not isinstance(schema, Mapping):
        raise ValueError("structured_output_json_schema_must_be_object")
    normalized, normalization_policy = _normalize_schema_proven_json_containers(payload, schema)
    normalized_root_wrapper, root_wrapper_changed = _normalize_schema_proven_root_item_wrapper(
        normalized,
        schema,
    )
    if not isinstance(normalized_root_wrapper, dict):
        raise ValueError("structured_output_payload_must_be_json_object")
    normalized = normalized_root_wrapper
    if root_wrapper_changed:
        normalization_policy = (
            f"{normalization_policy}+schema_proven_root_item_wrapper_v1"
            if normalization_policy != "none"
            else "schema_proven_root_item_wrapper_v1"
        )
    normalized_item_wrapper, item_wrapper_changed = _normalize_schema_proven_singleton_item_wrapper(
        normalized,
        schema,
    )
    if not isinstance(normalized_item_wrapper, dict):
        raise ValueError("structured_output_payload_must_be_json_object")
    normalized = normalized_item_wrapper
    if item_wrapper_changed:
        normalization_policy = (
            f"{normalization_policy}+schema_proven_singleton_item_wrapper_v1"
            if normalization_policy != "none"
            else "schema_proven_singleton_item_wrapper_v1"
        )
    normalized_map_chain, map_chain_changed, map_chain_paths = _normalize_schema_proven_map_item_chain(
        normalized,
        schema,
    )
    if not isinstance(normalized_map_chain, dict):
        raise ValueError("structured_output_payload_must_be_json_object")
    normalized = normalized_map_chain
    normalization_details: dict[str, Any] = {}
    if map_chain_changed:
        normalization_policy = (
            f"{normalization_policy}+schema_proven_map_item_chain_v1"
            if normalization_policy != "none"
            else "schema_proven_map_item_chain_v1"
        )
        if map_chain_paths:
            normalization_details["map_item_chain_paths"] = list(map_chain_paths)
    normalized_text_noise, text_noise_changed = _normalize_schema_closed_object_text_noise(normalized, schema)
    if not isinstance(normalized_text_noise, dict):
        raise ValueError("structured_output_payload_must_be_json_object")
    normalized = normalized_text_noise
    if text_noise_changed:
        normalization_policy = (
            f"{normalization_policy}+schema_proven_closed_object_text_noise_v1"
            if normalization_policy != "none"
            else "schema_proven_closed_object_text_noise_v1"
        )
    normalized_self_wrapper, self_wrapper_changed = _normalize_schema_proven_self_named_empty_wrapper(
        normalized,
        schema,
    )
    if not isinstance(normalized_self_wrapper, dict):
        raise ValueError("structured_output_payload_must_be_json_object")
    normalized = normalized_self_wrapper
    if self_wrapper_changed:
        normalization_policy = (
            f"{normalization_policy}+schema_proven_self_named_empty_wrapper_v1"
            if normalization_policy != "none"
            else "schema_proven_self_named_empty_wrapper_v1"
        )
    normalized_duplicate_root, duplicate_root_members = _normalize_schema_proven_duplicate_root_members(
        normalized,
        schema,
    )
    normalized = normalized_duplicate_root
    if duplicate_root_members:
        normalization_policy = (
            f"{normalization_policy}+schema_proven_duplicate_root_members_v1"
            if normalization_policy != "none"
            else "schema_proven_duplicate_root_members_v1"
        )
        normalization_details["duplicate_root_members"] = list(duplicate_root_members)
    (
        normalized_displaced,
        displaced_changed,
        relocation_evidence,
        displaced_defaults_applied,
    ) = _normalize_schema_proven_displaced_root_members(normalized, schema)
    normalized = normalized_displaced
    if displaced_changed:
        normalization_policy = (
            f"{normalization_policy}+schema_proven_displaced_root_members_v1"
            if normalization_policy != "none"
            else "schema_proven_displaced_root_members_v1"
        )
        normalization_details["displaced_root_relocations"] = list(relocation_evidence)
        if displaced_defaults_applied:
            normalization_policy += "+required_empty_container_defaults_v1"
    coerced = _coerce_structured_output_payload_defaults(normalized, schema)
    if coerced != normalized and "required_empty_container_defaults_v1" not in normalization_policy.split("+"):
        normalization_policy = (
            f"{normalization_policy}+required_empty_container_defaults_v1"
            if normalization_policy != "none"
            else "required_empty_container_defaults_v1"
        )
    errors = sorted(
        Draft202012Validator(dict(schema)).iter_errors(coerced),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if not errors:
        return coerced, normalization_policy, normalization_details
    first = errors[0]
    path = ".".join(str(part) for part in first.absolute_path) or "$"
    properties = schema.get("properties")
    declared = properties if isinstance(properties, Mapping) else {}
    unknown_root_shape = {str(key): type(value).__name__ for key, value in coerced.items() if str(key) not in declared}
    container: Any = coerced
    for part in first.absolute_path:
        if (isinstance(container, Mapping) and part in container) or (
            isinstance(container, list) and isinstance(part, int) and 0 <= part < len(container)
        ):
            container = container[part]
        else:
            break
    container_keys = sorted(str(key) for key in container) if isinstance(container, Mapping) else []
    required_at_failure = first.schema.get("required") if isinstance(first.schema, Mapping) else None
    declared_required = (
        sorted(str(key) for key in required_at_failure) if isinstance(required_at_failure, (list, tuple)) else []
    )
    # Provider response bodies remain redacted. Preserve only key/type and
    # schema structure at this validation boundary so a live failure can
    # distinguish omission, envelope drift, and stream corruption without
    # logging project content or secrets.
    logger.warning(
        "structured_output_schema_mismatch_shape: path=%s validator=%s container_type=%s "
        "container_keys=%s declared_required=%s unknown_root_shape=%s declared_root_keys=%s",
        path,
        str(first.validator),
        type(container).__name__,
        container_keys,
        declared_required,
        json.dumps(unknown_root_shape, ensure_ascii=False, sort_keys=True),
        sorted(str(key) for key in declared),
    )
    raise ValueError(f"structured_output_payload_schema_mismatch:{path}:{first.message}")


def _validate_payload(
    payload: Mapping[str, Any],
    plan: StructuredOutputTransportPlan,
) -> dict[str, Any]:
    """Coerce empty-container defaults, then fail-closed on residual schema errors."""

    return _validate_payload_with_normalization(payload, plan)[0]


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
    payload, normalization_policy, normalization_details = _validate_payload_with_normalization(payload, plan)
    payload_json = _canonical_json(payload)
    evidence = _transport_evidence(
        plan,
        payload_json=payload_json,
        call_id=_tool_call_id(result_call),
        schema_normalization_policy=normalization_policy,
        schema_normalization_details=normalization_details,
    )
    normalized = _ValidatedStructuredOutputResponse(response)
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

    __slots__ = (
        "_buffered_chunks",
        "_call_id",
        "_payload_json",
        "_plan",
        "_schema_normalization_details",
        "_schema_normalization_policy",
    )

    def __init__(self, plan: StructuredOutputTransportPlan) -> None:
        self._plan = plan
        self._buffered_chunks: list[dict[str, Any]] = []
        self._payload_json: str | None = None
        self._call_id = ""
        self._schema_normalization_details: dict[str, Any] = {}
        self._schema_normalization_policy = "none"

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
            (
                coerced,
                self._schema_normalization_policy,
                self._schema_normalization_details,
            ) = _validate_payload_with_normalization(args, self._plan)
            self._payload_json = _canonical_json(coerced)
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
            schema_normalization_policy=self._schema_normalization_policy,
            schema_normalization_details=self._schema_normalization_details,
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
    "project_validated_structured_output_raw_response",
    "require_exact_structured_output_tool_surface",
    "resolve_structured_output_transport",
    "trusted_structured_output_response_evidence",
    "trusted_structured_output_stream_evidence",
    "validate_structured_output_content",
    "validate_structured_output_stream_tool_call",
]
