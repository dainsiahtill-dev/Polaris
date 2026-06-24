"""Pure predicates + verdict dataclasses for ``ToolBatchExecutor.execute_tool_batch``.

This module hosts the side-effect-free decision-tree predicates extracted from the
``execute_tool_batch`` god-function (G5 behavior-preserving decomposition, blueprint
``REMAINING_03_execute-tool-batch.md``).

Hard rules for everything in this module:
- PURE: no I/O, no event emission, no ledger mutation, no raising of contract
  violations. Predicates return verdict objects; the orchestrator owns every
  ``raise`` / ``emit_event`` / ledger mutation in unchanged order (preserves the
  emitted-event sequence and ADR-0071 ordering).
- Deps imported DIRECTLY from canonical sources (never via the parent module) to
  avoid no-implicit-reexport ``[attr-defined]`` mypy errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    MutationTargetGuardViolation,
    extract_invocation_tool_name,
    resolve_mutation_target_guard_violation,
    tool_batch_has_authoritative_write_invocation,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryMode
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.phase_manager import Phase

# Tool-name sets used by the flag/exemption predicates. Copied verbatim from the
# original inline definitions in ``execute_tool_batch`` to preserve behavior.
DIRECT_READ_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "repo_read_head",
        "repo_read_slice",
        "repo_read_tail",
        "repo_read_around",
        "repo_read_range",
    }
)

BROAD_EXPLORATION_TOOLS: frozenset[str] = frozenset(
    {
        "glob",
        "repo_tree",
        "repo_rg",
        "grep",
        "search_code",
        "ripgrep",
        "find",
        "list_directory",
    }
)


@dataclass(frozen=True)
class BarrierVerdict:
    """Verdict for the read-write barrier predicate.

    ``violation_message`` is non-None ONLY when the orchestrator must raise the
    mixed read+write ``RuntimeError``. ``bypassed_overlap`` carries the overlap set
    so the orchestrator can log it (matching the original warning) when a tool is
    marked as both read and write.
    """

    violation_message: str | None
    bypassed_overlap: frozenset[str]


def evaluate_read_write_barrier(invocations: list[Any], *, bypass_read_write_barrier: bool) -> BarrierVerdict:
    """Evaluate the Read-Write Barrier with contract and overlap bypasses.

    Returns a verdict only; the orchestrator owns the raise/log. Behavior is
    byte-identical to the original inline block: explicitly bypassed batches are
    exempt; a tool marked as both read and write bypasses the strict barrier
    (overlap set is surfaced for logging); otherwise a mixed read+write batch
    yields a violation message identical to the original.
    """
    if bypass_read_write_barrier:
        return BarrierVerdict(violation_message=None, bypassed_overlap=frozenset())

    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    has_read = False
    has_write = False
    read_tools_invoked: list[str] = []
    write_tools_invoked: list[str] = []

    for invocation in invocations:
        tname = extract_invocation_tool_name(invocation)
        if not tname:
            continue
        spec = ToolSpecRegistry.get(tname)
        if spec:
            if spec.is_read_tool():
                has_read = True
                read_tools_invoked.append(tname)
            if spec.is_write_tool():
                has_write = True
                write_tools_invoked.append(tname)

    if has_read and has_write:
        overlap = set(read_tools_invoked) & set(write_tools_invoked)
        if overlap:
            return BarrierVerdict(violation_message=None, bypassed_overlap=frozenset(overlap))
        return BarrierVerdict(
            violation_message=(
                "single_batch_contract_violation: Cannot mix Read tools "
                f"({','.join(set(read_tools_invoked))}) and Write tools "
                f"({','.join(set(write_tools_invoked))}) in the same parallel batch. "
                "Please wait for read results before writing."
            ),
            bypassed_overlap=frozenset(),
        )
    return BarrierVerdict(violation_message=None, bypassed_overlap=frozenset())


@dataclass(frozen=True)
class InvocationFlags:
    """Computed flags shared by the downstream gates (single source of truth)."""

    tool_names: tuple[str, ...]
    non_empty_tool_names: tuple[str, ...]
    has_broad_exploration: bool
    has_write: bool
    only_broad_exploration: bool
    has_direct_read: bool


def compute_invocation_flags(invocations: list[Any]) -> InvocationFlags:
    """Compute the invocation flags exactly as the original inline block did.

    Must be called AFTER all ``invocations`` rebinds (path rewrite, edit_blocks fill,
    out-of-scope filter, implementing-phase partial block) so the flags reflect the
    latest binding.
    """
    tool_names = tuple(extract_invocation_tool_name(inv) for inv in invocations)
    non_empty_tool_names = tuple(name for name in tool_names if name)
    has_broad_exploration = any(name in BROAD_EXPLORATION_TOOLS for name in tool_names)
    has_write = tool_batch_has_authoritative_write_invocation(invocations)
    only_broad_exploration = bool(non_empty_tool_names) and all(
        name in BROAD_EXPLORATION_TOOLS for name in non_empty_tool_names
    )
    has_direct_read = any(name in DIRECT_READ_TOOLS for name in non_empty_tool_names)
    return InvocationFlags(
        tool_names=tool_names,
        non_empty_tool_names=non_empty_tool_names,
        has_broad_exploration=has_broad_exploration,
        has_write=has_write,
        only_broad_exploration=only_broad_exploration,
        has_direct_read=has_direct_read,
    )


@dataclass(frozen=True)
class PhaseSnapshot:
    """Snapshot of the phase-manager state read at the top of the decision tree.

    Copies the EXACT private reads (``_turns_in_current_phase`` /
    ``_max_turns_per_phase``) that the original inline block used (FIX-20250421-v3).
    """

    current_phase: Phase
    is_implementing: bool
    is_verifying: bool
    is_exploring: bool
    is_content_gathered: bool
    is_materialize: bool
    turns_in_phase: int
    max_turns_per_phase: int


def read_phase_snapshot(ledger: TurnLedger) -> PhaseSnapshot:
    """Read the phase snapshot verbatim from the ledger (pure read, no mutation)."""
    current_phase = ledger.phase_manager.current_phase
    is_materialize = (
        ledger._original_delivery_mode == DeliveryMode.MATERIALIZE_CHANGES.value
        or getattr(ledger.delivery_contract, "mode", None) == DeliveryMode.MATERIALIZE_CHANGES
    )
    return PhaseSnapshot(
        current_phase=current_phase,
        is_implementing=current_phase == Phase.IMPLEMENTING,
        is_verifying=current_phase == Phase.VERIFYING,
        is_exploring=current_phase == Phase.EXPLORING,
        is_content_gathered=current_phase == Phase.CONTENT_GATHERED,
        is_materialize=is_materialize,
        turns_in_phase=ledger.phase_manager._turns_in_current_phase,
        max_turns_per_phase=ledger.phase_manager._max_turns_per_phase,
    )


@dataclass(frozen=True)
class ContractDecision:
    """ALLOW / RAISE verdict for a contract gate.

    When ``error_type`` is None the orchestrator raises a bare ``RuntimeError``
    with ``message``; when ``error_type`` is set the orchestrator routes through
    ``_raise_contract_violation`` (which emits an ErrorEvent BEFORE raising).
    ``ALLOW`` is the ``message is None`` case.
    """

    message: str | None = None
    error_type: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def should_raise(self) -> bool:
        return self.message is not None


_ALLOW = ContractDecision()


def check_materialize_exploring_text_block(snapshot: PhaseSnapshot, invocations: list[Any]) -> str | None:
    """MATERIALIZE_CHANGES + EXPLORING with no tool calls → raise message, else None.

    (FIX-20250421-v3 text-output interception.) Pure; orchestrator owns the warn-log
    and the raise.
    """
    if snapshot.is_exploring and snapshot.is_materialize and not invocations:
        return (
            "single_batch_contract_violation: "
            "MATERIALIZE_CHANGES mode requires tool execution in EXPLORING phase. "
            "You must call read_file/glob/repo_rg to explore, then write_file/edit_file to modify. "
            "Text-only responses are not allowed."
        )
    return None


def is_verification_read_exemption(
    snapshot: PhaseSnapshot,
    flags: InvocationFlags,
    *,
    recent_edit_failure: bool,
) -> bool:
    """Phase-2 weak-model exemption (2026-06-10): direct-read-only batch after a recent
    edit failure bypasses the CONTENT_GATHERED write gate. Pure.

    ``recent_edit_failure`` is computed by the caller via
    ``_recent_edit_failure_in_context(context)`` so this predicate stays pure.
    """
    return (
        snapshot.is_content_gathered
        and snapshot.is_materialize
        and not flags.has_write
        and flags.has_direct_read
        and not flags.has_broad_exploration
        and recent_edit_failure
    )


def check_exploration_streak_block(
    latest_user: str,
    snapshot: PhaseSnapshot,
    flags: InvocationFlags,
) -> ContractDecision:
    """exploration_streak_hard_block gate (verbatim). Returns a RAISE verdict (with
    ``error_type``) so the orchestrator emits the ErrorEvent then raises."""
    streak_marker = "EXPLORATION_STREAK_HARD_BLOCK" in latest_user
    if (
        streak_marker
        and snapshot.is_exploring
        and flags.only_broad_exploration
        and not flags.has_direct_read
        and not flags.has_write
    ):
        return ContractDecision(
            message=(
                "single_batch_contract_violation: exploration_streak_hard_block active. "
                "Do not emit only glob/repo_rg/list_directory/repo_tree again. "
                "You must call read_file (or a write tool) this turn."
            ),
            error_type="exploration_streak_hard_block",
            metadata={
                "phase": "exploring",
                "tool_names": list(flags.non_empty_tool_names),
                "has_direct_read": flags.has_direct_read,
                "has_write": flags.has_write,
            },
        )
    return _ALLOW


@dataclass(frozen=True)
class ImplementingBlockOutcome:
    """Outcome of the implementing-phase broad-exploration block (FIX-20250421).

    Exactly one of: ``raise_message`` set (hard block), or ``rewritten_invocations``
    set (partial block — caller rebinds invocations + sets the ledger flag), or
    neither (no-op).
    """

    raise_message: str | None
    rewritten_invocations: list[Any] | None
    trigger_ledger_flag: bool


def apply_implementing_phase_exploration_block(
    snapshot: PhaseSnapshot,
    flags: InvocationFlags,
    invocations: list[Any],
) -> ImplementingBlockOutcome:
    """Implementing-phase broad-exploration handling (FIX-20250421). Pure: returns a
    new invocations list for the partial-block path (never mutates in place)."""
    if not (snapshot.is_implementing and flags.has_broad_exploration and not flags.has_write):
        return ImplementingBlockOutcome(raise_message=None, rewritten_invocations=None, trigger_ledger_flag=False)
    filtered_invocations = [
        inv for inv in invocations if extract_invocation_tool_name(inv) not in BROAD_EXPLORATION_TOOLS
    ]
    if not filtered_invocations:
        return ImplementingBlockOutcome(
            raise_message=(
                "single_batch_contract_violation: "
                "in implementing phase, broad exploration tools (glob/repo_tree/repo_rg) "
                "are not allowed. Use write_file/edit_file to materialize changes."
            ),
            rewritten_invocations=None,
            trigger_ledger_flag=False,
        )
    modified_invocations: list[Any] = []
    for inv in invocations:
        tname = extract_invocation_tool_name(inv)
        if tname in BROAD_EXPLORATION_TOOLS:
            modified_invocations.append(
                {
                    **inv,
                    "_implementing_phase_blocked": True,
                    "_blocked_reason": f"Tool '{tname}' blocked in implementing phase. Use write tools.",
                }
            )
        else:
            modified_invocations.append(inv)
    return ImplementingBlockOutcome(
        raise_message=None,
        rewritten_invocations=modified_invocations,
        trigger_ledger_flag=True,
    )


@dataclass(frozen=True)
class ContentGatheredGateOutcome:
    """Outcome of the CONTENT_GATHERED readiness gate (STEP 7).

    ``decision`` is the ALLOW/RAISE verdict (RAISE carries the
    ``content_gathered_write_required`` error_type + metadata, so the orchestrator
    routes through ``_raise_contract_violation``). ``log`` is a discriminator so the
    orchestrator can emit the EXACT log line that the original inline block emitted
    at each branch (``"timeout_degradation"`` warning, ``"needs_plan_allow"`` info)
    without the predicate doing any I/O.
    """

    decision: ContractDecision
    log: str | None = None


def evaluate_content_gathered_gate(
    *,
    snapshot: PhaseSnapshot,
    flags: InvocationFlags,
    ledger: TurnLedger,
    context: list[dict[str, Any]],
    verification_read_exemption: bool,
    enable_modification_contract: bool,
) -> ContentGatheredGateOutcome:
    """CONTENT_GATHERED + MATERIALIZE_CHANGES readiness gate (FIX-20250422-v3 + SUPER
    + disabled-flag v2 fallback).

    Branch order (byte-identical to the original inline block):
      guard not active → ALLOW
      enable_modification_contract:
        READY_TO_WRITE → RAISE (write required)
        NEEDS_PLAN, turns >= max → RAISE (timeout, degraded) [log: timeout_degradation]
        NEEDS_PLAN, turns <  max → ALLOW [log: needs_plan_allow]
      else (disabled):
        turns >= 2 → RAISE (v2 fallback)
        turns <  2 → ALLOW

    Note: ``evaluate_modification_readiness`` may auto-promote a DRAFT contract to
    READY (Rule 5). That side effect is internal to the readiness evaluator and
    happens at the SAME logical point as the original, so behavior is preserved.
    """
    from polaris.cells.roles.kernel.internal.transaction.modification_contract import (
        ReadinessVerdict,
        evaluate_modification_readiness,
    )

    gate_active = (
        snapshot.is_content_gathered
        and snapshot.is_materialize
        and not flags.has_write
        and not verification_read_exemption
    )
    if not gate_active:
        return ContentGatheredGateOutcome(decision=_ALLOW)

    turns_in_phase = ledger.phase_manager._turns_in_current_phase
    max_turns_per_phase = ledger.phase_manager._max_turns_per_phase

    if enable_modification_contract:
        delivery_mode_value = (
            ledger.delivery_contract.mode.value
            if hasattr(ledger.delivery_contract.mode, "value")
            else str(ledger.delivery_contract.mode)
        )
        verdict = evaluate_modification_readiness(
            contract=ledger.modification_contract,
            phase_value=snapshot.current_phase.value,
            delivery_mode_value=delivery_mode_value,
            turns_in_phase=turns_in_phase,
            max_turns_per_phase=max_turns_per_phase,
            conversation_context=context,
        )
        if verdict == ReadinessVerdict.READY_TO_WRITE:
            return ContentGatheredGateOutcome(
                decision=ContractDecision(
                    message=(
                        "single_batch_contract_violation: CONTENT_GATHERED phase requires write tools. "
                        "Your modification plan is confirmed. "
                        "You MUST call write_file/edit_file to execute your plan now. "
                        "Reading more files is blocked."
                    ),
                    error_type="content_gathered_write_required",
                    metadata={
                        "phase": "content_gathered",
                        "verdict": verdict.value,
                        "contract_status": ledger.modification_contract.status.value,
                        "turns_in_phase": turns_in_phase,
                        "tool_names": list(flags.non_empty_tool_names),
                        "has_write": flags.has_write,
                    },
                )
            )
        if turns_in_phase >= max_turns_per_phase:
            return ContentGatheredGateOutcome(
                decision=ContractDecision(
                    message=(
                        "single_batch_contract_violation: CONTENT_GATHERED phase timeout. "
                        "You have spent too many turns reading without declaring a modification plan. "
                        "You MUST call write_file/edit_file to materialize changes NOW. "
                        "Reading more files is blocked."
                    ),
                    error_type="content_gathered_write_required",
                    metadata={
                        "phase": "content_gathered",
                        "verdict": verdict.value,
                        "contract_status": ledger.modification_contract.status.value,
                        "turns_in_phase": turns_in_phase,
                        "tool_names": list(flags.non_empty_tool_names),
                        "has_write": flags.has_write,
                        "degraded": True,
                    },
                ),
                log="timeout_degradation",
            )
        return ContentGatheredGateOutcome(decision=_ALLOW, log="needs_plan_allow")

    if turns_in_phase >= 2:
        return ContentGatheredGateOutcome(
            decision=ContractDecision(
                message=(
                    "single_batch_contract_violation: CONTENT_GATHERED phase requires write tools. "
                    "You have already read file contents for multiple turns. "
                    "You MUST call write_file/edit_file to materialize changes. "
                    "Reading more files is blocked. Emit write tools now."
                ),
                error_type="content_gathered_write_required",
                metadata={
                    "phase": "content_gathered",
                    "turns_in_phase": turns_in_phase,
                    "tool_names": list(flags.non_empty_tool_names),
                    "has_write": flags.has_write,
                },
            )
        )
    return ContentGatheredGateOutcome(decision=_ALLOW)


@dataclass(frozen=True)
class MutationGuardPlan:
    """Plan for the mutation write-guard + target-guard (STEP 12).

    Pure verdicts only — the orchestrator owns every ``raise`` / ``logger.warning`` /
    ``ledger.record_mutation_guard_warning`` so emitted-event order and ledger
    side-effect order are byte-identical to the original inline block.

    - ``guard_mode``: 'warn' / 'strict' / (other config value), AFTER the
      implementing-phase strict upgrade (FIX-20250421).
    - ``no_write_violation``: True when a mutation is required but the batch has no
      authoritative write invocation (the orchestrator raises on strict / warns+records
      on warn).
    - ``target_guard_violation``: the violation message from
      ``resolve_mutation_target_guard_violation`` (or None) when a mutation is required.
    """

    guard_mode: str
    no_write_violation: bool
    target_guard_violation: MutationTargetGuardViolation | str | None


def compute_mutation_guard_plan(
    *,
    snapshot: PhaseSnapshot,
    flags: InvocationFlags,
    invocations: list[Any],
    requires_mutation: bool,
    configured_guard_mode: str,
    latest_user_request: str,
    contract_target_files: list[str],
) -> MutationGuardPlan:
    """Compute the mutation guard plan (guard_mode upgrade + both guard verdicts).

    Byte-identical to the original inline computation: guard_mode starts from config,
    upgrades to 'strict' in implementing phase when broad exploration was attempted
    without a write; the no-write violation and target-guard violation are only
    evaluated when ``requires_mutation`` is True.
    """
    guard_mode = configured_guard_mode
    if snapshot.is_implementing and flags.has_broad_exploration and not flags.has_write:
        guard_mode = "strict"

    no_write_violation = requires_mutation and not tool_batch_has_authoritative_write_invocation(invocations)

    target_guard_violation: MutationTargetGuardViolation | str | None = None
    if requires_mutation:
        target_guard_violation = resolve_mutation_target_guard_violation(
            latest_user_request,
            invocations,
            additional_allowed_targets=contract_target_files,
        )

    return MutationGuardPlan(
        guard_mode=guard_mode,
        no_write_violation=no_write_violation,
        target_guard_violation=target_guard_violation,
    )


def check_verifying_phase_requires_verification(
    snapshot: PhaseSnapshot,
    invocations: list[Any],
    context: list[dict[str, Any]],
) -> str | None:
    """Verifying-phase hard constraint (FIX-20250421 + SUPER bypass). Returns a raise
    message or None. Pure."""
    from polaris.cells.roles.kernel.internal.transaction.constants import VERIFICATION_TOOLS
    from polaris.cells.roles.kernel.internal.transaction.modification_contract import (
        _conversation_has_super_mode_markers,
    )

    if snapshot.is_verifying and not _conversation_has_super_mode_markers(context):
        tool_names = [extract_invocation_tool_name(inv) for inv in invocations]
        has_verification = any(t in VERIFICATION_TOOLS for t in tool_names)
        if not has_verification:
            return (
                "single_batch_contract_violation: "
                "verifying-phase-requires-verification: In verifying phase, "
                "you must call execute_command to run tests (pytest, etc.) "
                "or verify the fix manually. No verification tool detected — ending session."
            )
    return None
