"""Pure, diagnosis-bound edits of untrusted CE drafts; never execution authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

_MAX_EDITS = 16
_MAX_PATH_DEPTH = 16


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _parent(candidate: dict[str, Any], path: list[str | int]) -> dict[str, Any]:
    current: Any = candidate
    for part in path[:-1]:
        if (isinstance(part, str) and isinstance(current, dict) and part in current) or (
            type(part) is int and isinstance(current, list) and 0 <= part < len(current)
        ):
            current = cast(Any, current)[part]
        else:
            raise ValueError("ce_schema_patch_parent_changed")
    if not isinstance(current, dict) or not path or not isinstance(path[-1], str):
        raise ValueError("ce_schema_patch_requires_existing_object_parent")
    return current


def _has_schema_reference(value: Any) -> bool:
    if isinstance(value, Mapping):
        if any(isinstance(value.get(key), str) for key in ("$ref", "$dynamicRef")):
            return True
        return any(_has_schema_reference(child) for child in value.values())
    if isinstance(value, list):
        return any(_has_schema_reference(child) for child in value)
    return False


@dataclass(frozen=True)
class CESchemaResidualPatchPlan:
    """Frozen serialized draft and exact edit schema; owns no durable state."""

    _candidate_json: str
    _full_schema_json: str
    _patch_schema_json: str

    @property
    def base_candidate_hash(self) -> str:
        return hashlib.sha256(self._candidate_json.encode("utf-8")).hexdigest()

    @property
    def patch_schema(self) -> dict[str, Any]:
        return json.loads(self._patch_schema_json)

    def provider_context(self) -> dict[str, Any]:
        """Return fresh copies, explicitly untrusted, with no capability claim."""
        operations = self.patch_schema["properties"]["edits"]["prefixItems"]
        return {
            "base_candidate_hash": self.base_candidate_hash,
            "candidate_is_untrusted_draft": True,
            "execution_authority": False,
            "candidate": json.loads(self._candidate_json),
            "required_edits": [
                {
                    "operation": item["properties"]["operation"]["const"],
                    "path": item["properties"]["path"]["const"],
                }
                for item in operations
            ],
        }

    def compose(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        """Validate exact edits, preserve all other data, revalidate original schema."""
        errors = list(Draft202012Validator(self.patch_schema).iter_errors(dict(patch)))
        if errors:
            raise ValueError("ce_schema_patch_payload_rejected:" + errors[0].message)
        candidate = json.loads(self._candidate_json)
        for edit in patch["edits"]:
            path = edit["path"]
            parent = _parent(candidate, path)
            key = path[-1]
            if edit["operation"] == "remove":
                if key not in parent:
                    raise ValueError("ce_schema_patch_remove_target_changed")
                del parent[key]
            else:
                if key in parent:
                    raise ValueError("ce_schema_patch_add_would_overwrite")
                parent[key] = json.loads(_canonical(edit["value"]))
        full_errors = list(Draft202012Validator(json.loads(self._full_schema_json)).iter_errors(candidate))
        if full_errors:
            raise ValueError("ce_schema_patch_full_schema_rejected:" + full_errors[0].message)
        return candidate


def plan_ce_schema_residual_patch(
    *, candidate: Mapping[str, Any], schema: Mapping[str, Any]
) -> CESchemaResidualPatchPlan | None:
    """Plan only missing required and explicitly forbidden object-member edits.

    Type/enum/combinator failures, ambiguous pattern properties, invalid parents,
    oversized edit sets, and unsupported diagnostics stay on strict reconstruction.
    This function neither changes the candidate nor accepts a partial portfolio.
    """
    try:
        Draft202012Validator.check_schema(dict(schema))
    except SchemaError as exc:
        raise ValueError("ce_schema_patch_schema_invalid") from exc
    errors = list(Draft202012Validator(dict(schema)).iter_errors(dict(candidate)))
    if not errors or any(error.validator not in {"required", "additionalProperties"} for error in errors):
        return None
    edits: dict[str, dict[str, Any]] = {}
    for error in errors:
        parent_path = list(error.absolute_path)
        if any(not isinstance(part, str) and type(part) is not int for part in parent_path):
            return None
        if not isinstance(error.instance, Mapping) or not isinstance(error.schema, Mapping):
            return None
        properties = error.schema.get("properties", {})
        if not isinstance(properties, Mapping):
            return None
        if error.validator == "required":
            if not isinstance(error.validator_value, list):
                return None
            members = [name for name in error.validator_value if name not in error.instance]
            operation = "add"
        else:
            if error.validator_value is not False or error.schema.get("patternProperties"):
                return None
            members = [name for name in error.instance if name not in properties]
            operation = "remove"
        if not members:
            return None
        for name in members:
            if not isinstance(name, str) or not name:
                return None
            path = [*parent_path, name]
            if len(path) > _MAX_PATH_DEPTH:
                return None
            value_schema = properties.get(name)
            if operation == "add" and (not isinstance(value_schema, Mapping) or _has_schema_reference(value_schema)):
                return None
            fields: dict[str, Any] = {"operation": {"const": operation}, "path": {"const": path}}
            required = ["operation", "path"]
            if operation == "add":
                fields["value"] = dict(cast(Mapping[str, Any], value_schema))
                required.append("value")
            edit_schema = {
                "type": "object",
                "additionalProperties": False,
                "properties": fields,
                "required": required,
            }
            key = _canonical(path)
            if key in edits and edits[key] != edit_schema:
                return None
            edits[key] = edit_schema
    if not 1 <= len(edits) <= _MAX_EDITS:
        return None
    candidate_json = _canonical(dict(candidate))
    ordered_edits = [edits[key] for key in sorted(edits)]
    patch_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "base_candidate_hash": {"const": hashlib.sha256(candidate_json.encode("utf-8")).hexdigest()},
            "edits": {
                "type": "array",
                "prefixItems": ordered_edits,
                "items": False,
                "minItems": len(ordered_edits),
                "maxItems": len(ordered_edits),
            },
        },
        "required": ["base_candidate_hash", "edits"],
    }
    return CESchemaResidualPatchPlan(candidate_json, _canonical(dict(schema)), _canonical(patch_schema))
