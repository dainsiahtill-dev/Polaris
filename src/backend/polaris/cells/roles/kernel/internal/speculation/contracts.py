"""Private non-executable contracts for speculation shadow keys."""

from __future__ import annotations

import hashlib
import json
from dataclasses import InitVar, dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Mapping


def _freeze_shadow_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_shadow_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_shadow_value(item) for item in value)
    return value


def _tag_shadow_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"kind": "null"}
    if type(value) is bool:
        return {"kind": "scalar", "scalar_type": "boolean", "value": value}
    if type(value) is int:
        return {"kind": "scalar", "scalar_type": "integer", "value": value}
    if type(value) is float:
        return {"kind": "scalar", "scalar_type": "float", "value": value.hex()}
    if type(value) is str:
        return {"kind": "scalar", "scalar_type": "string", "value": value}
    if isinstance(value, Mapping):
        return {
            "kind": "map",
            "entries": [
                {"key": key, "value": _tag_shadow_value(item)}
                for key, item in sorted(value.items(), key=lambda entry: entry[0].encode("utf-8"))
            ],
        }
    if isinstance(value, tuple):
        return {"kind": "sequence", "items": [_tag_shadow_value(item) for item in value]}
    raise TypeError(f"unsupported synthetic shadow argument type: {type(value)!r}")


@dataclass(frozen=True, slots=True)
class SyntheticShadowToolKeyV1:
    """A non-executable, immutable key for private speculation shadow state."""

    source_tool_call_id: str = field(compare=False)
    canonical_tool_name: str
    shadow_phase: Literal["candidate", "resolver", "write_phase"]
    _shadow_arguments: InitVar[Mapping[str, Any] | None] = None
    shadow_key_hash: str = field(init=False)
    executable: Literal[False] = field(init=False, default=False)

    def __post_init__(self, _shadow_arguments: Mapping[str, Any] | None) -> None:
        if not self.source_tool_call_id:
            raise ValueError("source_tool_call_id must be non-empty")
        if not self.canonical_tool_name.startswith("__"):
            raise ValueError("synthetic shadow canonical_tool_name must be private")

        # Arguments never escape this construction boundary. ``normalize_args``
        # requires a concrete dict, so create it only for that existing API.
        from polaris.cells.roles.kernel.internal.speculation.fingerprints import normalize_args

        normalized = normalize_args(self.canonical_tool_name, dict(_shadow_arguments or {}))
        frozen_arguments = MappingProxyType({key: _freeze_shadow_value(value) for key, value in normalized.items()})
        payload = {
            "kind": "synthetic_shadow_tool_key.v1",
            "canonical_tool_name": self.canonical_tool_name,
            "shadow_phase": self.shadow_phase,
            "arguments": _tag_shadow_value(frozen_arguments),
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        object.__setattr__(self, "shadow_key_hash", hashlib.sha256(encoded.encode("utf-8")).hexdigest())

    @classmethod
    def build(
        cls,
        *,
        source_tool_call_id: str,
        canonical_tool_name: str,
        shadow_phase: Literal["candidate", "resolver", "write_phase"],
        arguments: Mapping[str, Any],
    ) -> SyntheticShadowToolKeyV1:
        return cls(
            source_tool_call_id=source_tool_call_id,
            canonical_tool_name=canonical_tool_name,
            shadow_phase=shadow_phase,
            _shadow_arguments=arguments,
        )
