"""Pure interpretation and restoration of already-captured ToolSpec evidence.

These helpers do not read the registry or grant execution authority. Callers
retain registration, raw-name and enclosing owner/effect binding checks.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Literal, cast

from polaris.kernelone.tool_execution.contracts import (
    CapturedToolSpecSnapshotV1,
    FrozenMapEntryV1,
    FrozenMapV1,
    FrozenNullV1,
    FrozenScalarV1,
    FrozenSequenceV1,
    FrozenToolSpecNodeV1,
    frozen_node_to_value,
)


@dataclass(frozen=True, slots=True)
class CapturedToolSemanticsV1:
    """Immutable semantics; errors remain conservative mutation candidates."""

    effect_type: Literal["read", "write", "async"]
    execution_mode: Literal["readonly_parallel", "write_serial", "async_receipt"]
    normalization_required: bool
    error_code: Literal["captured_tool_semantics_invalid"] | None


def _invalid_semantics() -> CapturedToolSemanticsV1:
    return CapturedToolSemanticsV1("write", "write_serial", True, "captured_tool_semantics_invalid")


def classify_captured_tool_semantics(snapshot: CapturedToolSpecSnapshotV1) -> CapturedToolSemanticsV1:
    """Interpret category fields together without consulting live registry state.

    This preserves the existing captured semantic rules. Registration and snapshot
    consistency are separate obligations; a read label alone is not authority.
    """
    effective_spec = frozen_node_to_value(snapshot.canonical_effective_spec)
    if not isinstance(effective_spec, dict):
        return _invalid_semantics()
    semantic_values: set[str] = set()
    category = effective_spec.get("category")
    if category is not None:
        if not isinstance(category, str):
            return _invalid_semantics()
        semantic_values.add(category.strip().lower())
    categories = effective_spec.get("categories")
    if categories is not None:
        if not isinstance(categories, list) or not all(isinstance(value, str) for value in categories):
            return _invalid_semantics()
        semantic_values.update(value.strip().lower() for value in categories)
    effect_type = effective_spec.get("effect_type")
    if effect_type is not None:
        if not isinstance(effect_type, str):
            return _invalid_semantics()
        semantic_values.add(effect_type.strip().lower())
    semantic_effects: dict[str, Literal["read", "write", "async"]] = {
        "read": "read",
        "write": "write",
        "async": "async",
        "exec": "write",
        "execute": "write",
        "delete": "write",
        "mutation": "write",
        "mutate": "write",
    }
    if not semantic_values or any(value not in semantic_effects for value in semantic_values):
        return _invalid_semantics()
    effects = {semantic_effects[value] for value in semantic_values}
    if len(effects) != 1:
        return _invalid_semantics()
    resolved_effect = effects.pop()
    if resolved_effect == "read":
        return CapturedToolSemanticsV1("read", "readonly_parallel", False, None)
    if resolved_effect == "async":
        return CapturedToolSemanticsV1("async", "async_receipt", True, None)
    return CapturedToolSemanticsV1("write", "write_serial", True, None)


def captured_tool_spec_snapshot_is_consistent(snapshot: object) -> bool:
    """Recheck the existing immutable hash and captured name/alias bindings."""
    if not isinstance(snapshot, CapturedToolSpecSnapshotV1):
        return False
    try:
        canonical_snapshot = CapturedToolSpecSnapshotV1(
            raw_tool_name=snapshot.raw_tool_name,
            canonical_tool_name=snapshot.canonical_tool_name,
            registered=snapshot.registered,
            canonical_effective_spec=snapshot.canonical_effective_spec,
            canonical_name_view=snapshot.canonical_name_view,
            alias_binding_view=snapshot.alias_binding_view,
        )
        if canonical_snapshot != snapshot:
            return False
        if snapshot.registered:
            canonical_names = frozen_node_to_value(snapshot.canonical_name_view)
            alias_bindings = frozen_node_to_value(snapshot.alias_binding_view)
            if (
                not isinstance(canonical_names, list)
                or not all(isinstance(name, str) for name in canonical_names)
                or snapshot.canonical_tool_name not in canonical_names
                or not isinstance(alias_bindings, dict)
                or alias_bindings.get(snapshot.raw_tool_name) != snapshot.canonical_tool_name
            ):
                return False
        return True
    except (AttributeError, TypeError, ValueError):
        return False


def _restore_frozen_node(value: object) -> FrozenToolSpecNodeV1:
    if not isinstance(value, Mapping):
        raise ValueError("frozen node must be a record")
    kind = value.get("kind")
    if kind == "null" and set(value) == {"kind"}:
        return FrozenNullV1()
    if kind == "scalar" and set(value) == {"kind", "scalar_type", "value"}:
        return FrozenScalarV1(
            cast(Literal["string", "boolean", "integer", "float"], value["scalar_type"]),
            cast(str | bool | int | float, value["value"]),
        )
    if kind == "sequence" and set(value) == {"kind", "items"} and type(value["items"]) is list:
        return FrozenSequenceV1(tuple(_restore_frozen_node(item) for item in value["items"]))
    if kind == "map" and set(value) == {"kind", "entries"} and type(value["entries"]) is list:
        entries = []
        for entry in value["entries"]:
            if not isinstance(entry, Mapping) or set(entry) != {"key", "value"} or type(entry["key"]) is not str:
                raise ValueError("invalid frozen map entry")
            entries.append(FrozenMapEntryV1(entry["key"], _restore_frozen_node(entry["value"])))
        return FrozenMapV1(tuple(entries))
    raise ValueError("invalid frozen node record")


def restore_captured_tool_spec_snapshot(value: object) -> CapturedToolSpecSnapshotV1 | None:
    """Restore the existing JSON snapshot only when its supplied hashes match.

    Recomputed hashes never replace missing or contradictory persisted hashes.
    This validates captured evidence, not registration or enclosing owner scope.
    """
    if not isinstance(value, Mapping) or set(value) != {
        "raw_tool_name",
        "canonical_tool_name",
        "registered",
        "canonical_effective_spec",
        "canonical_name_view",
        "alias_binding_view",
        "tool_spec_hash",
        "canonical_name_view_hash",
        "alias_binding_hash",
        "snapshot_hash",
    }:
        return None
    if (
        type(value["raw_tool_name"]) is not str
        or type(value["canonical_tool_name"]) is not str
        or type(value["registered"]) is not bool
    ):
        return None
    try:
        effective_spec = _restore_frozen_node(value["canonical_effective_spec"])
        names = _restore_frozen_node(value["canonical_name_view"])
        aliases = _restore_frozen_node(value["alias_binding_view"])
        if (
            not isinstance(effective_spec, FrozenMapV1)
            or not isinstance(names, FrozenSequenceV1)
            or not isinstance(aliases, FrozenMapV1)
        ):
            return None
        snapshot = CapturedToolSpecSnapshotV1(
            raw_tool_name=value["raw_tool_name"],
            canonical_tool_name=value["canonical_tool_name"],
            registered=value["registered"],
            canonical_effective_spec=effective_spec,
            canonical_name_view=names,
            alias_binding_view=aliases,
        )
        if any(
            type(value[field]) is not str or value[field] != getattr(snapshot, field)
            for field in (
                "tool_spec_hash",
                "canonical_name_view_hash",
                "alias_binding_hash",
                "snapshot_hash",
            )
        ):
            return None
        if not captured_tool_spec_snapshot_is_consistent(snapshot):
            return None
        raw_wire = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        restored_wire = json.dumps(
            asdict(snapshot), sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        if raw_wire != restored_wire:
            return None
        return snapshot
    except (AttributeError, TypeError, ValueError):
        return None
