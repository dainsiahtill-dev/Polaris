from __future__ import annotations

from typing import Any

from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

from ...structured_output_transport import (
    STRUCTURED_OUTPUT_TOOL_NAME,
    STRUCTURED_OUTPUT_TRANSPORT_SCHEMA,
    StructuredOutputTransportPlan,
)
from ..response_types import PreparedLLMRequest
from ._constants import (
    _NO_TOOL_CONTRACT_CONTEXT_KEYS,
    _PROVIDER_PROTOCOL_COVERAGE_SCHEMA,
    _PROVIDER_PROTOCOL_SOURCE,
    _TOOL_REGISTRY_SOURCE,
)
from ._primitives import (
    _json_safe,
    _mapping,
    _stable_digest,
    _unique_strings,
)
from ._request_core import (
    _request_context,
    _request_options,
)


def _summarize_tool_schema(tool: Any) -> dict[str, Any]:
    if not isinstance(tool, dict):
        return {"type": type(tool).__name__, "name": "", "argument_keys": [], "required": []}
    function_payload = tool.get("function")
    function = function_payload if isinstance(function_payload, dict) else tool
    parameters_payload = function.get("parameters") if isinstance(function, dict) else {}
    parameters = parameters_payload if isinstance(parameters_payload, dict) else {}
    properties_payload = parameters.get("properties")
    properties = properties_payload if isinstance(properties_payload, dict) else {}
    required_payload = parameters.get("required")
    required = required_payload if isinstance(required_payload, list) else []
    return {
        "type": str(tool.get("type") or "function"),
        "name": str(function.get("name") or ""),
        "argument_keys": sorted(str(key) for key in properties),
        "required": [str(item) for item in required],
    }


def _summarize_response_format(response_format: Any) -> Any:
    if response_format is None:
        return None
    if not isinstance(response_format, dict):
        return _json_safe(response_format)
    summary: dict[str, Any] = {"type": response_format.get("type")}
    json_schema = response_format.get("json_schema")
    if isinstance(json_schema, dict):
        summary["json_schema_name"] = json_schema.get("name")
        schema = json_schema.get("schema")
        if isinstance(schema, dict):
            properties = schema.get("properties")
            if isinstance(properties, dict):
                summary["json_schema_property_keys"] = sorted(str(key) for key in properties)
    return _json_safe(summary)


def _tool_name_from_schema(tool: Any) -> str:
    if not isinstance(tool, dict):
        return ""
    function_payload = tool.get("function")
    function = function_payload if isinstance(function_payload, dict) else tool
    return str(function.get("name") or "").strip()


def _tool_names_from_payload(value: Any) -> list[str]:
    if isinstance(value, str):
        return _unique_strings(value)
    if isinstance(value, dict):
        direct_name = str(value.get("name") or "").strip()
        if direct_name:
            return [direct_name]
        function_payload = value.get("function")
        if isinstance(function_payload, dict):
            function_name = str(function_payload.get("name") or "").strip()
            if function_name:
                return [function_name]
        names: list[str] = []
        for key in ("required_tools", "tools", "allowed_tools", "available_tools"):
            names.extend(_tool_names_from_payload(value.get(key)))
        return _unique_strings(names)
    if isinstance(value, (list, tuple, set, frozenset)):
        item_names: list[str] = []
        for item in value:
            item_names.extend(_tool_names_from_payload(item))
        return _unique_strings(item_names)
    return []


def _canonical_tool_name(name: Any) -> str:
    token = str(name or "").strip()
    if not token:
        return ""
    try:
        return str(ToolSpecRegistry.get_canonical(token) or token).strip()
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
        return token


def _canonical_tool_names(values: Any) -> list[str]:
    return _unique_strings(
        [canonical for value in _tool_names_from_payload(values) if (canonical := _canonical_tool_name(value))]
    )


def _required_tool_names_from_payload(value: Any) -> list[str]:
    if isinstance(value, str):
        return _canonical_tool_names(value)
    if isinstance(value, dict):
        names: list[str] = []
        for key in (
            "required_tools",
            "task_required_tools",
            "must_call_tools",
            "mandatory_tools",
            "contract_required_tools",
            "tool_requirements",
        ):
            names.extend(_required_tool_names_from_payload(value.get(key)))
        return _unique_strings(names)
    if isinstance(value, (list, tuple, set, frozenset)):
        nested_names: list[str] = []
        for item in value:
            nested_names.extend(_required_tool_names_from_payload(item))
        return _unique_strings(nested_names)
    return []


def _allowed_tool_names_from_payload(value: Any) -> list[str]:
    if isinstance(value, str):
        return _canonical_tool_names(value)
    if isinstance(value, dict):
        names: list[str] = []
        direct_name = str(value.get("name") or "").strip()
        if direct_name:
            names.append(direct_name)
        function_payload = value.get("function")
        if isinstance(function_payload, dict):
            function_name = str(function_payload.get("name") or "").strip()
            if function_name:
                names.append(function_name)
        for key in ("allowed_tools", "available_tools", "offered_tools", "tools"):
            names.extend(_allowed_tool_names_from_payload(value.get(key)))
        return _unique_strings([canonical for name in names if (canonical := _canonical_tool_name(name))])
    if isinstance(value, (list, tuple, set, frozenset)):
        nested_names: list[str] = []
        for item in value:
            nested_names.extend(_allowed_tool_names_from_payload(item))
        return _unique_strings(nested_names)
    return []


def _available_tool_names(tool_schema_payload: Any) -> list[str]:
    if not isinstance(tool_schema_payload, list):
        return []
    return _unique_strings(
        [canonical for tool in tool_schema_payload if (canonical := _canonical_tool_name(_tool_name_from_schema(tool)))]
    )


def _tool_schema_properties(tool_schema: Any) -> dict[str, Any]:
    if not isinstance(tool_schema, dict):
        return {}
    function_payload = tool_schema.get("function")
    if not isinstance(function_payload, dict):
        return {}
    parameters = function_payload.get("parameters")
    if not isinstance(parameters, dict):
        return {}
    properties = parameters.get("properties")
    return dict(properties) if isinstance(properties, dict) else {}


def _provider_protocol_schema_coverage(
    tool_schema_payload: Any,
    *,
    tool_choice_payload: Any,
    plan: StructuredOutputTransportPlan | None,
) -> dict[str, Any]:
    """Bind one caller-owned result protocol to the exact provider surface.

    This protocol is deliberately separate from executable ToolSpecRegistry
    tools.  It returns typed content and is consumed before authorization,
    Tool Lifecycle, effect receipts, or mutation ports.
    """

    if plan is None:
        return {
            "schema_version": _PROVIDER_PROTOCOL_COVERAGE_SCHEMA,
            "active": False,
            "valid": True,
            "protocol_source": "",
            "tool_name": "",
            "transport": "",
            "strict": False,
            "executable_tool": False,
            "side_effect": False,
            "tool_lifecycle": False,
            "contract_hash": "",
            "tool_schema_hash": "",
            "observed_tool_schema_hash": "",
            "tool_choice_hash": "",
            "observed_tool_choice_hash": "",
            "failure_code": "",
        }

    tools = tool_schema_payload if isinstance(tool_schema_payload, list) else []
    expected_tool = plan.tool_definition
    expected_choice = plan.tool_choice
    actual_tool = tools[0] if len(tools) == 1 and isinstance(tools[0], dict) else None
    if not tools:
        failure_code = "provider_protocol_tool_missing"
    elif len(tools) != 1:
        failure_code = "provider_protocol_tool_surface_mixed"
    elif actual_tool != expected_tool:
        failure_code = "provider_protocol_tool_schema_drift"
    elif tool_choice_payload != expected_choice:
        failure_code = "provider_protocol_tool_choice_drift"
    else:
        failure_code = ""
    return {
        "schema_version": _PROVIDER_PROTOCOL_COVERAGE_SCHEMA,
        "active": True,
        "valid": not failure_code,
        "protocol_source": _PROVIDER_PROTOCOL_SOURCE,
        "transport_schema": STRUCTURED_OUTPUT_TRANSPORT_SCHEMA,
        "tool_name": STRUCTURED_OUTPUT_TOOL_NAME,
        "schema_name": plan.contract.schema_name,
        "transport": plan.contract.transport,
        "strict": plan.contract.strict,
        "executable_tool": False,
        "side_effect": False,
        "tool_lifecycle": False,
        "contract_hash": _stable_digest(plan.contract.to_context_projection()),
        "tool_schema_hash": _stable_digest(expected_tool),
        "observed_tool_schema_hash": _stable_digest(actual_tool) if actual_tool is not None else "",
        "tool_choice_hash": _stable_digest(expected_choice),
        "observed_tool_choice_hash": _stable_digest(tool_choice_payload),
        "failure_code": failure_code,
    }


def _tool_schema_registry_coverage(
    tool_schema_payload: Any,
    *,
    missing_required_tools: list[str],
    exempt_tool_schemas: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """Project registry provenance from the exact final provider tool surface.

    The provider request is authoritative.  Registry provenance must therefore
    be reconstructed from its offered schemas, not trusted from caller-supplied
    context flags.  B3.5 performs the stricter byte/shape validation again at
    qualification time; this projection supplies the auditable source and
    alias coverage that qualification binds to that same request.
    """

    executable_tool_schemas = (
        [
            tool_schema
            for tool_schema in tool_schema_payload
            if not (isinstance(tool_schema, dict) and any(tool_schema == exempt for exempt in exempt_tool_schemas))
        ]
        if isinstance(tool_schema_payload, list)
        else []
    )
    if not executable_tool_schemas:
        return {
            "registry_source": "",
            "aliases_present": False,
            "arg_aliases_present": False,
            "schema_hash": "",
            "missing_schema_tools": _unique_strings(missing_required_tools),
        }

    missing_schema_tools = list(missing_required_tools)
    aliases_present = True
    arg_aliases_present = True
    for tool_schema in executable_tool_schemas:
        raw_name = _tool_name_from_schema(tool_schema)
        canonical_name = _canonical_tool_name(raw_name)
        try:
            captured = ToolSpecRegistry.capture_effective_spec(raw_name)
            schema_with_aliases = ToolSpecRegistry.get_llm_schema(
                canonical_name,
                include_arg_aliases=True,
                deterministic=True,
            )
            schema_without_aliases = ToolSpecRegistry.get_llm_schema(
                canonical_name,
                include_arg_aliases=False,
                deterministic=True,
            )
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
            captured = None
            schema_with_aliases = None
            schema_without_aliases = None

        if (
            captured is None
            or captured.registered is not True
            or not canonical_name
            or schema_with_aliases is None
            or schema_without_aliases is None
        ):
            missing_schema_tools.append(canonical_name or raw_name)
            aliases_present = False
            arg_aliases_present = False
            continue

        expected_alias_properties = set(_tool_schema_properties(schema_with_aliases)).difference(
            _tool_schema_properties(schema_without_aliases)
        )
        actual_properties = set(_tool_schema_properties(tool_schema))
        if not expected_alias_properties.issubset(actual_properties):
            arg_aliases_present = False

    missing_schema_tools = _unique_strings(missing_schema_tools)
    if missing_schema_tools:
        aliases_present = False
        arg_aliases_present = False
    return {
        "registry_source": _TOOL_REGISTRY_SOURCE if not missing_schema_tools else "",
        "aliases_present": aliases_present,
        "arg_aliases_present": arg_aliases_present,
        "schema_hash": _stable_digest(executable_tool_schemas),
        "missing_schema_tools": missing_schema_tools,
    }


def _required_tool_names(ai_request: Any) -> list[str]:
    context_payload = _request_context(ai_request)
    names: list[str] = []
    for key in ("required_tools", "task_required_tools", "tool_requirements", "tool_contract"):
        names.extend(_required_tool_names_from_payload(context_payload.get(key)))
    return _unique_strings(names)


def _required_tools_exempt_reason(ai_request: Any, prepared: PreparedLLMRequest) -> str:
    """Reason string when this request's tool surface is disabled BY DESIGN.

    A finalization-style call (tool_choice ``none``/``disabled``, an explicit
    no-tool contract, or a TransactionKernel forced tool disable) exposes zero
    callable tools on purpose. Required-tool semantics inherited from the turn
    context must not be reported as ``missing_required_tools`` for such a call:
    the tools are not missing — they are not exposed by design. An empty tool
    surface WITHOUT one of these explicit disable signals is still treated as
    required-tool pruning and keeps failing coverage.
    """

    options = _request_options(ai_request, prepared)
    tool_choice = str(options.get("tool_choice") or "").strip().lower()
    if tool_choice in {"none", "disabled"}:
        return "tool_choice_disabled_by_design"
    context_payload = _request_context(ai_request)
    if any(bool(context_payload.get(key)) for key in _NO_TOOL_CONTRACT_CONTEXT_KEYS):
        return "tool_contract_requires_no_tool_calls"
    tool_contract = _mapping(context_payload.get("tool_contract"))
    if any(bool(tool_contract.get(key)) for key in _NO_TOOL_CONTRACT_CONTEXT_KEYS):
        return "tool_contract_requires_no_tool_calls"
    forced_definitions = context_payload.get("_transaction_kernel_forced_tool_definitions")
    forced_choice = str(context_payload.get("_transaction_kernel_forced_tool_choice") or "").strip().lower()
    if isinstance(forced_definitions, list) and not forced_definitions and forced_choice == "none":
        return "transaction_kernel_tools_disabled"
    return ""


def _allowed_tool_names(ai_request: Any) -> list[str]:
    context_payload = _request_context(ai_request)
    names: list[str] = []
    for key in ("allowed_tools", "available_tools", "offered_tools", "tool_policy", "tool_contract"):
        names.extend(_allowed_tool_names_from_payload(context_payload.get(key)))
    return _unique_strings(names)
