"""Repair result/planning summaries and directed-effect plan contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from polaris.cells.director.runtime.public.contracts._diagnostics_receipts import (
    RepairAdvisoryV1,
    RepairDiagnosticV1,
    RepairReceiptV1,
    _repair_diagnostic_v1_to_dict,
)
from polaris.cells.director.runtime.public.contracts._helpers import (
    _require_non_empty,
    _to_dict_copy,
    _to_tuple_str,
)
from polaris.cells.director.runtime.public.directed_effect_contracts import (
    DirectedEffectImmutableItemsV1,
    DirectedEffectImmutableMapV1,
    DirectedEffectImmutableSequenceV1,
    DirectedEffectImmutableValueV1,
    hash_directed_effect_arguments,
    require_directed_effect_immutable_items,
)


@dataclass(frozen=True)
class DirectorRepairResultV1:
    """Result shape for Director Runtime repair execution."""

    ok: bool
    receipts: tuple[RepairReceiptV1, ...] = ()
    residual_diagnostics: tuple[RepairDiagnosticV1, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipts", tuple(self.receipts or ()))
        object.__setattr__(self, "residual_diagnostics", tuple(self.residual_diagnostics or ()))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed DirectorRepairResultV1 must include error_code or error_message")


@dataclass(frozen=True)
class DirectorRepairPatchSummaryV1:
    """Public per-file patch projection for repair planning."""

    path: str
    operation_ids: tuple[str, ...]
    before_hash: str
    after_hash: str
    changed: bool
    content_after: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _require_non_empty("path", self.path).replace("\\", "/"))
        object.__setattr__(self, "operation_ids", _to_tuple_str(list(self.operation_ids)))
        object.__setattr__(self, "before_hash", _require_non_empty("before_hash", self.before_hash))
        object.__setattr__(self, "after_hash", _require_non_empty("after_hash", self.after_hash))
        object.__setattr__(self, "changed", bool(self.changed))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable patch summary."""

        return {
            "path": self.path,
            "operation_ids": list(self.operation_ids),
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "changed": self.changed,
            "content_after": self.content_after,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairCompositionIssueV1:
    """Public fail-closed patch composition issue."""

    code: str
    message: str
    path: str | None = None
    operation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _require_non_empty("code", self.code))
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "path", str(self.path).replace("\\", "/") if self.path is not None else None)
        object.__setattr__(self, "operation_ids", _to_tuple_str(list(self.operation_ids)))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable composition issue."""

        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "operation_ids": list(self.operation_ids),
        }


@dataclass(frozen=True)
class DirectorRepairPlanSummaryV1:
    """Public repair plan summary without exposing internal kernel classes."""

    plan_id: str
    rule_id: str
    source_tool: str
    mode: str
    risk_level: str
    diagnostic_count: int
    operation_count: int
    advisor_note_count: int = 0
    agi_execution_authority: bool = False
    advisory_authoritative: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _require_non_empty("plan_id", self.plan_id))
        object.__setattr__(self, "rule_id", _require_non_empty("rule_id", self.rule_id))
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "mode", _require_non_empty("mode", self.mode))
        object.__setattr__(self, "risk_level", _require_non_empty("risk_level", self.risk_level))
        object.__setattr__(self, "diagnostic_count", max(0, int(self.diagnostic_count)))
        object.__setattr__(self, "operation_count", max(0, int(self.operation_count)))
        object.__setattr__(self, "advisor_note_count", max(0, int(self.advisor_note_count)))
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "advisory_authoritative", False)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable plan summary."""

        return {
            "plan_id": self.plan_id,
            "rule_id": self.rule_id,
            "source_tool": self.source_tool,
            "mode": self.mode,
            "risk_level": self.risk_level,
            "diagnostic_count": self.diagnostic_count,
            "operation_count": self.operation_count,
            "advisor_note_count": self.advisor_note_count,
            "agi_execution_authority": False,
            "advisory_authoritative": False,
        }


@dataclass(frozen=True)
class DirectorRepairCompositionSummaryV1:
    """Public repair composition summary without exposing PatchComposer types."""

    ok: bool
    patches: tuple[DirectorRepairPatchSummaryV1, ...] = ()
    issues: tuple[DirectorRepairCompositionIssueV1, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "patches", tuple(self.patches or ()))
        object.__setattr__(self, "issues", tuple(self.issues or ()))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable composition summary."""

        return {
            "ok": self.ok,
            "patch_count": len(self.patches),
            "issue_count": len(self.issues),
            "changed_paths": [patch.path for patch in self.patches if patch.changed],
            "patches": [patch.to_dict() for patch in self.patches],
            "issues": [issue.to_dict() for issue in self.issues],
        }


DirectorRepairEffectToolNameV1 = Literal["write_file", "edit_file", "delete_file"]
DirectorRepairEffectContingencyKindV1 = Literal["forward", "rollback"]


def _require_sha256_hex(name: str, value: str) -> str:
    normalized = _require_non_empty(name, value)
    if (
        len(normalized) != 64
        or normalized != normalized.lower()
        or any(char not in "0123456789abcdef" for char in normalized)
    ):
        raise ValueError(f"{name} must be a lowercase 64-character SHA-256 hex digest")
    return normalized


def _require_relative_effect_path(value: str) -> str:
    normalized = _require_non_empty("target_path", value).replace("\\", "/")
    parts = normalized.split("/")
    if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
        raise ValueError("target_path must be workspace-relative")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("target_path must be canonical and traversal-free")
    return normalized


def _directed_effect_value_to_json(value: DirectedEffectImmutableValueV1) -> Any:
    if isinstance(value, DirectedEffectImmutableMapV1):
        return {key: _directed_effect_value_to_json(item) for key, item in value.items}
    if isinstance(value, DirectedEffectImmutableSequenceV1):
        return [_directed_effect_value_to_json(item) for item in value.items]
    if isinstance(value, tuple):
        return [_directed_effect_value_to_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class DirectorRepairEffectV1:
    """One immutable, state-bound Director tool effect or rollback contingency."""

    call_id: str
    operation_id: str
    tool_name: DirectorRepairEffectToolNameV1
    arguments: DirectedEffectImmutableItemsV1
    contingency_kind: DirectorRepairEffectContingencyKindV1
    target_path: str
    expected_before_hash: str
    expected_after_hash: str
    exists_before: bool
    exists_after: bool
    activates_after_call_id: str | None = None
    arguments_hash: str = field(init=False)
    schema_version: str = "director.repair_effect.v1"

    def __post_init__(self) -> None:
        call_id = _require_non_empty("call_id", self.call_id)
        operation_id = _require_non_empty("operation_id", self.operation_id)
        if self.tool_name not in {"write_file", "edit_file", "delete_file"}:
            raise ValueError("tool_name must be write_file, edit_file, or delete_file")
        arguments = require_directed_effect_immutable_items("arguments", self.arguments)
        if self.contingency_kind not in {"forward", "rollback"}:
            raise ValueError("contingency_kind must be forward or rollback")
        target_path = _require_relative_effect_path(self.target_path)
        if type(self.exists_before) is not bool or type(self.exists_after) is not bool:
            raise TypeError("exists_before and exists_after must be bool")
        activates_after_call_id = (
            _require_non_empty("activates_after_call_id", self.activates_after_call_id)
            if self.activates_after_call_id is not None
            else None
        )
        if self.contingency_kind == "forward" and activates_after_call_id is not None:
            raise ValueError("forward effects must not declare activates_after_call_id")
        if self.contingency_kind == "rollback" and activates_after_call_id is None:
            raise ValueError("rollback effects require activates_after_call_id")

        argument_map = dict(arguments)
        expected_keys = {
            "write_file": {"content", "file"},
            "edit_file": {"file", "replace", "search"},
            "delete_file": {"file"},
        }[self.tool_name]
        if set(argument_map) != expected_keys:
            raise ValueError(f"{self.tool_name} arguments must contain exactly {sorted(expected_keys)}")
        if argument_map.get("file") != target_path:
            raise ValueError("arguments file must match target_path")
        if not all(isinstance(value, str) for value in argument_map.values()):
            raise TypeError("repair effect tool arguments must be strings")

        object.__setattr__(self, "call_id", call_id)
        object.__setattr__(self, "operation_id", operation_id)
        object.__setattr__(self, "arguments", arguments)
        object.__setattr__(self, "target_path", target_path)
        object.__setattr__(
            self, "expected_before_hash", _require_sha256_hex("expected_before_hash", self.expected_before_hash)
        )
        object.__setattr__(
            self, "expected_after_hash", _require_sha256_hex("expected_after_hash", self.expected_after_hash)
        )
        object.__setattr__(self, "activates_after_call_id", activates_after_call_id)
        object.__setattr__(self, "arguments_hash", hash_directed_effect_arguments(arguments))
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))

    def immutable_identity(self) -> DirectedEffectImmutableMapV1:
        """Return the complete canonical effect identity used by the plan hash."""

        return DirectedEffectImmutableMapV1(
            items=(
                ("activates_after_call_id", self.activates_after_call_id),
                ("arguments", DirectedEffectImmutableMapV1(items=self.arguments)),
                ("arguments_hash", self.arguments_hash),
                ("call_id", self.call_id),
                ("contingency_kind", self.contingency_kind),
                ("expected_after_hash", self.expected_after_hash),
                ("expected_before_hash", self.expected_before_hash),
                ("exists_after", self.exists_after),
                ("exists_before", self.exists_before),
                ("operation_id", self.operation_id),
                ("schema_version", self.schema_version),
                ("target_path", self.target_path),
                ("tool_name", self.tool_name),
            )
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable immutable effect projection."""

        return {
            "schema_version": self.schema_version,
            "call_id": self.call_id,
            "operation_id": self.operation_id,
            "tool_name": self.tool_name,
            "arguments": {key: _directed_effect_value_to_json(value) for key, value in self.arguments},
            "arguments_hash": self.arguments_hash,
            "contingency_kind": self.contingency_kind,
            "activates_after_call_id": self.activates_after_call_id,
            "target_path": self.target_path,
            "expected_before_hash": self.expected_before_hash,
            "expected_after_hash": self.expected_after_hash,
            "exists_before": self.exists_before,
            "exists_after": self.exists_after,
        }


def hash_director_repair_effect_plan(
    *,
    plan_id: str,
    source_tool: str,
    round_number: int,
    effects: tuple[DirectorRepairEffectV1, ...],
    schema_version: str = "director.repair_effect_plan.v1",
    owner_cell: str = "director.runtime",
) -> str:
    """Hash one complete immutable repair effect plan in a distinct domain."""

    if not isinstance(effects, tuple):
        raise TypeError("effects must be an immutable tuple")
    return hash_directed_effect_arguments(
        (
            ("domain", "director_repair_effect_plan_v1"),
            ("effects", DirectedEffectImmutableSequenceV1(tuple(effect.immutable_identity() for effect in effects))),
            ("plan_id", _require_non_empty("plan_id", plan_id)),
            ("round_number", round_number),
            ("schema_version", _require_non_empty("schema_version", schema_version)),
            ("source_tool", _require_non_empty("source_tool", source_tool)),
            ("owner_cell", _require_non_empty("owner_cell", owner_cell)),
        )
    )


@dataclass(frozen=True, slots=True)
class DirectorRepairEffectPlanV1:
    """One server-derived repair round with predeclared forward and rollback effects."""

    plan_id: str
    source_tool: str
    effects: tuple[DirectorRepairEffectV1, ...]
    round_number: Literal[1] = 1
    effect_count: int = field(init=False)
    plan_hash: str = field(init=False)
    schema_version: str = "director.repair_effect_plan.v1"
    owner_cell: str = "director.runtime"

    def __post_init__(self) -> None:
        plan_id = _require_non_empty("plan_id", self.plan_id)
        source_tool = _require_non_empty("source_tool", self.source_tool)
        if not isinstance(self.effects, tuple):
            raise TypeError("effects must be an immutable tuple")
        if type(self.round_number) is not int or self.round_number != 1:
            raise ValueError("round_number must be exactly 1 for DEO-2C")
        if not all(isinstance(effect, DirectorRepairEffectV1) for effect in self.effects):
            raise TypeError("effects must contain DirectorRepairEffectV1 values")
        call_ids = tuple(effect.call_id for effect in self.effects)
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("effect call_id values must be unique")
        forward_effects = tuple(effect for effect in self.effects if effect.contingency_kind == "forward")
        rollback_effects = tuple(effect for effect in self.effects if effect.contingency_kind == "rollback")
        forward_by_id = {effect.call_id: effect for effect in forward_effects}
        rollback_activation_ids: list[str] = []
        for rollback in rollback_effects:
            activated_by = forward_by_id.get(str(rollback.activates_after_call_id or ""))
            if activated_by is None:
                raise ValueError("rollback activates_after_call_id must reference a forward effect")
            if activated_by.target_path != rollback.target_path:
                raise ValueError("rollback effect must target the path of its activating forward effect")
            rollback_activation_ids.append(str(rollback.activates_after_call_id))
        if set(rollback_activation_ids) != set(forward_by_id):
            raise ValueError("every forward repair effect must have a rollback contingency")
        if len(rollback_activation_ids) != len(set(rollback_activation_ids)):
            raise ValueError("each forward repair effect must have exactly one rollback contingency")

        object.__setattr__(self, "plan_id", plan_id)
        object.__setattr__(self, "source_tool", source_tool)
        object.__setattr__(self, "effect_count", len(self.effects))
        schema_version = _require_non_empty("schema_version", self.schema_version)
        owner_cell = _require_non_empty("owner_cell", self.owner_cell)
        if schema_version != "director.repair_effect_plan.v1":
            raise ValueError("schema_version must be director.repair_effect_plan.v1")
        if owner_cell != "director.runtime":
            raise ValueError("owner_cell must be director.runtime")
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "owner_cell", owner_cell)
        object.__setattr__(
            self,
            "plan_hash",
            hash_director_repair_effect_plan(
                plan_id=plan_id,
                source_tool=source_tool,
                round_number=self.round_number,
                effects=self.effects,
                schema_version=schema_version,
                owner_cell=owner_cell,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the complete execution-grade repair effect plan."""

        return {
            "schema_version": self.schema_version,
            "owner_cell": self.owner_cell,
            "plan_id": self.plan_id,
            "source_tool": self.source_tool,
            "round_number": self.round_number,
            "effect_count": self.effect_count,
            "plan_hash": self.plan_hash,
            "effects": [effect.to_dict() for effect in self.effects],
        }


def validate_director_repair_effect_plan(plan: DirectorRepairEffectPlanV1) -> DirectorRepairEffectPlanV1:
    """Reconstruct one plan exactly and reject forged derived hashes or identities."""

    if type(plan) is not DirectorRepairEffectPlanV1:
        raise TypeError("plan must be exactly DirectorRepairEffectPlanV1")
    canonical_effects = tuple(
        DirectorRepairEffectV1(
            call_id=effect.call_id,
            operation_id=effect.operation_id,
            tool_name=effect.tool_name,
            arguments=effect.arguments,
            contingency_kind=effect.contingency_kind,
            target_path=effect.target_path,
            expected_before_hash=effect.expected_before_hash,
            expected_after_hash=effect.expected_after_hash,
            exists_before=effect.exists_before,
            exists_after=effect.exists_after,
            activates_after_call_id=effect.activates_after_call_id,
            schema_version=effect.schema_version,
        )
        for effect in plan.effects
    )
    if any(
        canonical.arguments_hash != supplied.arguments_hash
        for canonical, supplied in zip(canonical_effects, plan.effects, strict=True)
    ):
        raise ValueError("repair effect arguments_hash mismatch")
    canonical_plan = DirectorRepairEffectPlanV1(
        plan_id=plan.plan_id,
        source_tool=plan.source_tool,
        effects=canonical_effects,
        round_number=plan.round_number,
        schema_version=plan.schema_version,
        owner_cell=plan.owner_cell,
    )
    if canonical_plan.plan_hash != plan.plan_hash:
        raise ValueError("repair effect plan_hash mismatch")
    if canonical_plan != plan:
        raise ValueError("repair effect plan is not canonical")
    return canonical_plan


@dataclass(frozen=True)
class DirectorRepairPlanningResultV1:
    """Public deterministic repair planning result."""

    ok: bool
    planned: bool
    source_tool: str
    diagnostic_count: int
    diagnostics: tuple[RepairDiagnosticV1, ...] = ()
    plan_summary: DirectorRepairPlanSummaryV1 | None = None
    composition_summary: DirectorRepairCompositionSummaryV1 = field(
        default_factory=lambda: DirectorRepairCompositionSummaryV1(ok=False)
    )
    effect_plan: DirectorRepairEffectPlanV1 | None = None
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    schema_version: str = "director.repair_planning_result.v1"
    owner_cell: str = "director.runtime"
    execution_boundary: str = "director_authorized_tools_only"
    agi_execution_authority: bool = False
    advisory_authoritative: bool = False
    director_tool_execution_required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "planned", bool(self.planned))
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "diagnostic_count", max(0, int(self.diagnostic_count)))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics or ()))
        if self.effect_plan is not None:
            if not isinstance(self.effect_plan, DirectorRepairEffectPlanV1):
                raise TypeError("effect_plan must be DirectorRepairEffectPlanV1")
            if self.effect_plan.source_tool != self.source_tool:
                raise ValueError("effect_plan source_tool must match planning result")
            if self.plan_summary is not None and self.effect_plan.plan_id != self.plan_summary.plan_id:
                raise ValueError("effect_plan plan_id must match plan_summary")
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "error_code", str(self.error_code or "").strip() or None)
        object.__setattr__(self, "error_message", str(self.error_message or "").strip() or None)
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "advisory_authoritative", False)
        object.__setattr__(self, "director_tool_execution_required", True)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable planning payload."""

        return {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "planned": self.planned,
            "source_tool": self.source_tool,
            "diagnostic_count": self.diagnostic_count,
            "diagnostics": [_repair_diagnostic_v1_to_dict(diagnostic) for diagnostic in self.diagnostics],
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "advisory_authoritative": False,
            "director_tool_execution_required": True,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "plan_summary": self.plan_summary.to_dict() if self.plan_summary is not None else None,
            "composition_summary": self.composition_summary.to_dict(),
            "effect_plan": self.effect_plan.to_dict() if self.effect_plan is not None else None,
            "advisor_notes": [note.to_dict() for note in self.advisor_notes],
        }
