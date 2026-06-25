"""Policy checks for composed Director repair plans."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import CompositionResult, RepairPlan, RepairReceipt

_DEFAULT_FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bunittest\.skip\s*\(", re.IGNORECASE),
    re.compile(r"\bpytest\.skip\s*\(", re.IGNORECASE),
    re.compile(r"^\s*except\s*:\s*pass\s*$", re.IGNORECASE | re.MULTILINE),
)


@dataclass(frozen=True)
class RepairPolicyContext:
    """Policy context for one repair planning/execution pass."""

    allowed_paths: tuple[str, ...] = ()
    previous_receipts: tuple[RepairReceipt, ...] = ()
    max_rule_activations: int = 2
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyDecision:
    """Policy decision for a repair plan or composition result."""

    allowed: bool
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reasons": list(self.reasons)}


class RepairPolicyGate:
    """Fail-closed policy gate for deterministic repair plans."""

    def evaluate_plan(self, plan: RepairPlan, context: RepairPolicyContext | None = None) -> PolicyDecision:
        ctx = context or RepairPolicyContext()
        reasons: list[str] = []
        if plan.mode not in {"commit", "shadow"}:
            reasons.append("unsupported_mode")
        for note in plan.advisor_notes:
            if note.authoritative:
                reasons.append("advisor_note_marked_authoritative")
        if self._rule_activation_count(plan, ctx) >= max(1, ctx.max_rule_activations):
            reasons.append("rule_activation_cycle_breaker")
        allowed_paths = tuple(_normalize_prefix(path) for path in ctx.allowed_paths if _normalize_prefix(path))
        for operation in plan.operations:
            normalized = _normalize_prefix(operation.path)
            if not normalized:
                reasons.append("unsafe_operation_path")
            if allowed_paths and not any(_path_within(normalized, prefix) for prefix in allowed_paths):
                reasons.append(f"path_outside_allowed_scope:{normalized}")
        return PolicyDecision(allowed=not reasons, reasons=tuple(dict.fromkeys(reasons)))

    def evaluate_composition(self, plan: RepairPlan, composition: CompositionResult) -> PolicyDecision:
        reasons: list[str] = []
        if not composition.ok:
            reasons.append("composition_failed")
        for patch in composition.patches:
            if _contains_forbidden_pattern(patch.content_after):
                reasons.append(f"forbidden_repair_content:{patch.path}")
        return PolicyDecision(allowed=not reasons, reasons=tuple(dict.fromkeys(reasons)))

    @staticmethod
    def _rule_activation_count(plan: RepairPlan, context: RepairPolicyContext) -> int:
        files = {operation.path for operation in plan.operations}
        diagnostics = {diagnostic.diagnostic_id for diagnostic in plan.diagnostics}
        count = 0
        for receipt in context.previous_receipts:
            if receipt.rule_id != plan.rule_id:
                continue
            receipt_files = set(receipt.files_changed)
            receipt_diag_ids = {diagnostic.diagnostic_id for diagnostic in receipt.diagnostics}
            if files and receipt_files and files.isdisjoint(receipt_files):
                continue
            if diagnostics and receipt_diag_ids and diagnostics.isdisjoint(receipt_diag_ids):
                continue
            count += 1
        return count


def _contains_forbidden_pattern(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _DEFAULT_FORBIDDEN_PATTERNS)


def _normalize_prefix(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/").strip("/")
    if not normalized or normalized == "." or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


def _path_within(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix.rstrip('/')}/")
