"""Typed role structured-output contracts.

The contract is public because the calling cell owns the result schema.  The
roles kernel owns only transport and validation; it does not import
role-specific adapter models.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

ROLE_STRUCTURED_OUTPUT_CONTRACT_SCHEMA = "roles.kernel.structured_output_contract.v1"
STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY = "role_structured_output_contract"
_SCHEMA_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_MAX_SCHEMA_BYTES = 65_536


def _canonical_json_mapping(value: Mapping[str, Any], *, field_name: str) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}_must_be_json_serializable") from exc
    if len(encoded) > _MAX_SCHEMA_BYTES:
        raise ValueError(f"{field_name}_exceeds_max_bytes")
    decoded = json.loads(encoded.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError(f"{field_name}_must_be_mapping")
    return decoded


@dataclass(frozen=True, slots=True)
class RoleStructuredOutputContractV1:
    """One caller-owned JSON result contract transported by the roles kernel."""

    schema_name: str
    description: str
    json_schema: Mapping[str, Any]
    transport: Literal["provider_tool"] = "provider_tool"
    strict: bool = True

    def __post_init__(self) -> None:
        schema_name = str(self.schema_name or "").strip()
        if not _SCHEMA_NAME_PATTERN.fullmatch(schema_name):
            raise ValueError("schema_name_invalid")
        description = str(self.description or "").strip()
        if not description:
            raise ValueError("description_required")
        if not isinstance(self.json_schema, Mapping):
            raise TypeError("json_schema_must_be_mapping")
        schema = _canonical_json_mapping(self.json_schema, field_name="json_schema")
        if schema.get("type") != "object":
            raise ValueError("json_schema_type_must_be_object")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError("json_schema_properties_must_be_object")
        required = schema.get("required", [])
        if (
            not isinstance(required, list)
            or any(not isinstance(item, str) or not item.strip() for item in required)
            or len(set(required)) != len(required)
        ):
            raise ValueError("json_schema_required_must_be_unique_string_array")
        unknown_required = sorted(set(required).difference(properties))
        if unknown_required:
            raise ValueError(f"json_schema_required_property_missing:{','.join(unknown_required)}")
        if self.transport != "provider_tool":
            raise ValueError("structured_output_transport_unsupported")
        if self.strict is not True:
            raise ValueError("structured_output_contract_must_be_strict")
        object.__setattr__(self, "schema_name", schema_name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "json_schema", schema)

    def to_context_projection(self) -> dict[str, Any]:
        """Return the JSON-safe projection carried through roles.runtime."""

        return {
            "schema_version": ROLE_STRUCTURED_OUTPUT_CONTRACT_SCHEMA,
            "schema_name": self.schema_name,
            "description": self.description,
            "json_schema": _canonical_json_mapping(self.json_schema, field_name="json_schema"),
            "transport": self.transport,
            "strict": self.strict,
        }

    @classmethod
    def from_context_projection(
        cls,
        value: Mapping[str, Any],
    ) -> RoleStructuredOutputContractV1:
        """Rehydrate and validate one runtime context projection."""

        if not isinstance(value, Mapping):
            raise TypeError("structured_output_contract_projection_must_be_mapping")
        if str(value.get("schema_version") or "") != ROLE_STRUCTURED_OUTPUT_CONTRACT_SCHEMA:
            raise ValueError("structured_output_contract_schema_version_mismatch")
        json_schema = value.get("json_schema")
        if not isinstance(json_schema, Mapping):
            raise TypeError("structured_output_contract_json_schema_must_be_mapping")
        return cls(
            schema_name=str(value.get("schema_name") or ""),
            description=str(value.get("description") or ""),
            json_schema=json_schema,
            transport=str(value.get("transport") or ""),  # type: ignore[arg-type]
            strict=value.get("strict") is True,
        )


__all__ = [
    "ROLE_STRUCTURED_OUTPUT_CONTRACT_SCHEMA",
    "STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY",
    "RoleStructuredOutputContractV1",
]
