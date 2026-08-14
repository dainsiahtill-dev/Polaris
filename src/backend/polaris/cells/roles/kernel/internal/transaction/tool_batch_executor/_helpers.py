"""Helper functions and private types for tool batch execution.

Private implementation module of the tool_batch_executor package.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    FailureClassV1,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    build_tool_batch_lifecycle_receipt_from_sources,
    effect_receipts_from_batch_receipts,
)
from polaris.cells.director.runtime.public import DirectedEffectImmutableItemsV1
from polaris.cells.roles.kernel.internal.speculation.write_phases import WriteToolPhases
from polaris.cells.roles.kernel.internal.tool_batch_runtime import ToolBatchRuntime
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    extract_invocation_tool_name,
    extract_target_file_from_invocation_args,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.transaction.receipt_utils import (
    merge_batch_receipts,
    normalize_batch_receipts,
)
from polaris.cells.roles.kernel.internal.transaction.tool_call_audit_refs import tool_invocation_audit_ref
from polaris.cells.roles.kernel.public.directed_effect_contracts import (
    PreparedDirectedEffectBatchV1,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    _infer_effect_type,
)
from polaris.kernelone.tools.tool_kinds import DEPRECATED_WRITE_TOOLS

# Preserve historical logger identity (former single-module __name__).
logger = logging.getLogger(__package__ or __name__)


def _is_deo_abort_error(message: str) -> bool:
    """Return True when a RuntimeError is a Directed-Effect authorization abort."""

    token = str(message or "").strip()
    if not token:
        return False
    lowered = token.lower()
    return lowered.startswith("deo_") or lowered.startswith("directed_effect_")


# R149: advisory flock contention under multi-member DEO inventory admit maps
# (or previously failed to map) to these upstream codes.  Retry after yielding
# the event loop so concurrent settlement/heartbeat queries can release locks.
_TRANSIENT_DEO_PREPARE_UPSTREAM_CODES: frozenset[str] = frozenset(
    {
        "stream_lock_timeout",
        "lock_acquisition_timeout",
        "file_lock_timeout",
        "lock_timeout",
        # Pre-R149 taxonomy: lock_acquisition_timeout fell through to unknown.
        "fact_stream_unknown_failure",
    }
)
_DEO_PREPARE_LOCK_RETRY_ATTEMPTS = 4
_DEO_PREPARE_LOCK_RETRY_BASE_SECONDS = 0.05


def _deo_prepare_upstream_code(prepared: Any) -> str:
    """Extract TaskRuntime upstream_code from a lifecycle prepare denial."""

    for key, value in getattr(prepared, "upstream_evidence", None) or ():
        if str(key) == "upstream_code" and value:
            return str(value).strip()
    return ""


def _is_transient_deo_prepare_lock_failure(prepared: Any) -> bool:
    """Return True when prepare_batch failed on a retryable fact-stream lock."""

    if getattr(prepared, "status", None) == "ready" and getattr(prepared, "prepared_batch", None) is not None:
        return False
    upstream = _deo_prepare_upstream_code(prepared)
    if upstream in _TRANSIENT_DEO_PREPARE_UPSTREAM_CODES:
        return True
    # port_exception shells may wrap the raw lock code.
    if any(token in upstream for token in _TRANSIENT_DEO_PREPARE_UPSTREAM_CODES):
        return True
    error_code = str(getattr(prepared, "error_code", None) or "")
    return any(token in error_code for token in ("stream_lock_timeout", "lock_acquisition_timeout"))


def _seal_deo_abort_tool_lifecycle(
    *,
    workspace: str,
    run_id: str,
    task_id: str,
    turn_id: str,
    role_id: str,
    invocations: list[Any],
    metadata: Mapping[str, Any] | None,
    ledger: TurnLedger | None,
    error_code: str,
    provider_response_hash: str = "",
) -> dict[str, Any]:
    """Seal a blocked tool lifecycle receipt when DEO aborts before physical dispatch.

    R135: Claimed materialization that dies on ``deo_director_policy_denied`` (or
    sibling DEO aborts) must not leave Run Ledger as bare TOOL_LIFECYCLE_MISSING.
    This helper is best-effort and never swallows the original abort.

    Complexity:
        O(n) over decoded invocations for dropped-call refs and ledger append.
    """

    meta = dict(metadata) if isinstance(metadata, Mapping) else {}
    error_token = str(error_code or "directed_effect_policy_denied").strip() or "directed_effect_policy_denied"
    dropped_refs: list[dict[str, str]] = []
    for invocation in invocations:
        tool_name = extract_invocation_tool_name(invocation) or "unknown_tool"
        dropped_refs.append(
            tool_invocation_audit_ref(
                invocation,
                reason=error_token,
                tool_name=tool_name,
                target_file=extract_target_file_from_invocation_args(invocation),
            )
        )
    if not dropped_refs:
        dropped_refs = [{"tool_name": "write_file", "reason": error_token}]

    lifecycle = build_tool_batch_lifecycle_receipt_from_sources(
        run_id=str(run_id or ""),
        task_id=str(task_id or ""),
        turn_id=str(turn_id or ""),
        role=str(role_id or ""),
        provider_response_hash=str(provider_response_hash or meta.get("provider_response_hash") or ""),
        metadata=meta,
        decoded_tool_calls_count=len(invocations),
        receipts=[],
        dropped_tool_calls=dropped_refs,
        missing_receipt_reason=error_token,
    ).to_dict()
    # Authoritative DEO abort is a blocked post-decode outcome, not silent missing.
    lifecycle["dispatch_status"] = "blocked"
    lifecycle["failure_class"] = FailureClassV1.TOOL_RESULT_FAILED.value
    lifecycle["ok"] = False
    lifecycle["reason"] = error_token
    lifecycle["deo_abort"] = True
    lifecycle["deo_error_code"] = error_token

    if ledger is not None:
        ledger.anomaly_flags.append(
            {
                "type": "DEO_ABORT",
                "error_code": error_token,
                "failure_class": FailureClassV1.TOOL_RESULT_FAILED.value,
                "tool_call_lifecycle_receipt": dict(lifecycle),
                "turn_id": str(turn_id or ""),
            }
        )
    resolved_run_id = str(run_id or lifecycle.get("run_id") or "").strip()
    if not resolved_run_id and ledger is not None:
        resolved_run_id = str(getattr(ledger, "run_id", "") or "").strip()
    if not resolved_run_id:
        # Last-resort identity: never invent a fake run, but still keep anomaly on ledger.
        logger.warning(
            "R135: DEO-abort lifecycle sealed only in-memory (missing run_id) turn_id=%s error=%s",
            turn_id,
            error_token,
        )
        return lifecycle
    try:
        append_tool_call_lifecycle_event(
            AppendToolCallLifecycleEventCommandV1(
                workspace=workspace,
                run_id=resolved_run_id,
                task_id=str(task_id or ""),
                turn_id=str(turn_id or ""),
                role=str(role_id or ""),
                lifecycle_receipt=lifecycle,
                stage="tool_batch",
                project_id=str(task_id or ""),
                ok=False,
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.debug("R135: failed to append DEO-abort tool lifecycle to Run Ledger", exc_info=True)
    return lifecycle


@dataclass(frozen=True, slots=True)
class _PreparedDirectedEffectDispatchV1:
    """Whole mutation batch plus exact gateway-owned JobToken restrictions."""

    batch: PreparedDirectedEffectBatchV1
    restrictions_by_call_id: tuple[tuple[str, DirectedEffectImmutableItemsV1], ...]
    # Soft-denied members (call_id, tool_name, error_code) that must not abort
    # siblings. Empty when every mutation authorized.
    dropped_members: tuple[tuple[str, str, str], ...] = ()


def _is_mutation_for_speculative_routing(
    invocation: ToolInvocation,
    *,
    directed_effect_required: bool,
) -> bool:
    """Keep required DEO mutation routing independent of legacy name tables."""

    if directed_effect_required:
        return invocation.effect_type is not ToolEffectType.READ
    return WriteToolPhases.is_write_tool(str(invocation.tool_name))


# ---------------------------------------------------------------------------
# 路径辅助
# ---------------------------------------------------------------------------

_TOOL_NAME_CANONICAL_ALIASES = {
    "project_scaffolding": "project_scaffold",
}

_NO_WRITE_STRUCTURED_ROLES = frozenset({"pm", "chief_engineer", "architect", "qa"})
_NO_WRITE_STRUCTURED_FLAG = "DELIVERY_CONTRACT_ROLE_NO_WRITE_STRUCTURED_OUTPUT"


# Edit-tool failure signatures (verbatim substrings of executor error payloads).
# Used to recognize "the previous edit attempt failed" from the conversation so
# the mandated verification read can pass the CONTENT_GATHERED write gate.
_EDIT_FAILURE_MARKERS: tuple[str, ...] = (
    "identical search and replace",
    "Validation failed for",
    "No valid edit blocks",
    "missing required argument: blocks or start",
    "edit_blocks received prose/narration",
    "SEARCH text exactly matches file content",
    "Failed to parse edit blocks",
)


def _resolve_tool_batch_execution_identity(
    metadata: Mapping[str, Any],
    config: TransactionConfig,
) -> tuple[str, str, str]:
    """Resolve workspace/run/task identity without trusting model output.

    Provider decision metadata may carry transport evidence, but execution
    identity belongs to the immutable transaction configuration. Explicit
    metadata remains supported for internal deterministic callers and tests;
    absent fields fall back to the transaction authority.
    """

    workspace = str(metadata.get("workspace") or config.workspace or ".").strip() or "."
    run_id = str(metadata.get("run_id") or config.run_id or "").strip()
    task_id = str(metadata.get("task_id") or config.task_id or "").strip()
    return workspace, run_id, task_id


def _recent_edit_failure_in_context(context: Any, lookback: int = 8) -> bool:
    """Whether the tail of the conversation records a failed edit attempt."""
    try:
        tail = list(context)[-lookback:]
    except TypeError:
        return False
    for message in reversed(tail):
        if not isinstance(message, Mapping):
            continue
        content = str(message.get("content") or "")
        if any(marker in content for marker in _EDIT_FAILURE_MARKERS):
            return True
    return False


def _normalize_allowed_tool_name_alias(name: str) -> str:
    normalized = str(name or "").strip().lower().replace("-", "_")
    return _TOOL_NAME_CANONICAL_ALIASES.get(normalized, normalized)


def _tool_name_allowed_by_alias(tool_name: str, allowed_tool_names: set[str]) -> bool:
    if tool_name in allowed_tool_names:
        return True
    normalized_tool_name = _normalize_allowed_tool_name_alias(tool_name)
    if not normalized_tool_name:
        return False
    normalized_allowed = {_normalize_allowed_tool_name_alias(name) for name in allowed_tool_names}
    return normalized_tool_name in normalized_allowed


def _is_no_write_structured_turn(config: TransactionConfig, ledger: TurnLedger) -> bool:
    """Whether resolver already pinned this turn as a structured no-write role output."""
    role_id = str(getattr(config, "role_id", "") or "").strip().lower()
    if role_id not in _NO_WRITE_STRUCTURED_ROLES:
        return False
    contract = getattr(ledger, "delivery_contract", None)
    if contract is None:
        return False
    if getattr(contract, "mode", None) != DeliveryMode.PROPOSE_PATCH:
        return False
    if bool(getattr(contract, "requires_mutation", False)):
        return False
    return any(
        isinstance(flag, Mapping) and str(flag.get("type") or "").strip().upper() == _NO_WRITE_STRUCTURED_FLAG
        for flag in getattr(ledger, "anomaly_flags", [])
    )


def _tool_requires_existing_file(tool_name: str) -> bool:
    return tool_name in (
        {
            "read_file",
            "repo_read_head",
            "repo_read_slice",
            "repo_read_tail",
            "repo_read_around",
            "file_exists",
            "edit_file",
        }
        | DEPRECATED_WRITE_TOOLS
    )


_DIRECT_READ_TOOLS = {
    "read_file",
    "repo_read_head",
    "repo_read_slice",
    "repo_read_tail",
    "repo_read_around",
    "repo_read_range",
}


_LINE_RANGE_REPLACEMENT_KEYS = ("replace", "new_text", "new_content", "replacement", "code")
_FILE_ARGUMENT_KEYS = ("file", "path", "filepath", "file_path", "target_file", "target_path")


# Valid execution_mode values that downstream bucket filters recognize. Anything
# outside this set (None, empty string, unknown enum-like value, unsupported
# type) is treated as missing during normalization.
_VALID_TOOL_EXECUTION_MODES: frozenset[str] = frozenset({mode.value for mode in ToolExecutionMode})
_VALID_TOOL_EFFECT_TYPES: frozenset[str] = frozenset({effect.value for effect in ToolEffectType})


def _is_valid_execution_mode(raw_mode: Any) -> bool:
    """Whether ``raw_mode`` is a ToolExecutionMode-accepted bucket value.

    Accepts both Enum members and the canonical string form so callers that
    pass either flavor (turn_contracts ``ToolInvocation`` models use the Enum,
    raw ``Mapping`` invocations from the decoder often use the string) are
    recognized as already-annotated.
    """

    if isinstance(raw_mode, ToolExecutionMode):
        return True
    if isinstance(raw_mode, str):
        return raw_mode in _VALID_TOOL_EXECUTION_MODES
    return False


def _resolve_missing_execution_mode(invocation: Any) -> ToolExecutionMode:
    """Derive the canonical ``ToolExecutionMode`` for an invocation lacking one.

    Reuses :py:meth:`ToolBatchRuntime.classify_tool` — the kernel's single
    tool-classification truth source — so we never introduce a parallel
    classification table here. Read tools map to ``READONLY_PARALLEL``,
    async tools to ``ASYNC_RECEIPT``, and anything else falls back to
    ``WRITE_SERIAL`` (safe default: serial write barrier).

    An invocation with a missing/blank tool name still resolves to
    ``WRITE_SERIAL`` rather than raising, so a malformed call doesn't escalate
    into a hard contract violation; the bucket filter will route it through
    the authoritative write path where downstream effect policy gates
    inspect it again.
    """

    tool_name = extract_invocation_tool_name(invocation)
    if not tool_name:
        return ToolExecutionMode.WRITE_SERIAL
    try:
        return ToolBatchRuntime.classify_tool(tool_name)
    except (AttributeError, TypeError, ValueError):
        # Defensive: ``classify_tool`` should never raise on a str input, but
        # if a future change narrows its contract we still want the executor
        # to fail-closed via the safe default rather than crash mid-turn.
        return ToolExecutionMode.WRITE_SERIAL


def _set_invocation_execution_mode(invocation: Any, mode: ToolExecutionMode) -> Any:
    """Return a copy of ``invocation`` with ``execution_mode`` set to ``mode``.

    Mirrors the read-side shims (``_with_invocation_arguments``,
    ``_with_invocation_top_level_field``) — dict inputs get a shallow-copied
    dict, Mapping inputs fall through to ``dict(...)``, and opaque objects
    without a writable attribute are returned unchanged so the caller can
    still detect them and surface a diagnostic. The caller must always
    treat the result as best-effort: bucket filters re-read the value via
    ``inv.get("execution_mode")`` after normalization.
    """

    if isinstance(invocation, dict):
        updated = dict(invocation)
        updated["execution_mode"] = mode
        return updated
    if isinstance(invocation, Mapping):
        updated = dict(invocation)
        updated["execution_mode"] = mode
        return updated
    to_dict = getattr(invocation, "to_dict", None)
    if callable(to_dict):
        try:
            payload = to_dict()
        except (RuntimeError, TypeError, ValueError):
            return invocation
        if isinstance(payload, dict):
            updated = dict(payload)
            updated["execution_mode"] = mode
            return updated
    if hasattr(invocation, "execution_mode"):
        try:
            object.__setattr__(invocation, "execution_mode", mode) if hasattr(invocation, "__frozen__") else setattr(
                invocation, "execution_mode", mode
            )
        except (AttributeError, TypeError):
            return invocation
        return invocation
    return invocation


def _set_invocation_effect_type(invocation: Any, effect_type: Any) -> Any:
    """Return a copy of ``invocation`` with ``effect_type`` set to ``effect_type``.

    Same shape as :py:func:`_set_invocation_execution_mode` but writes the
    ``effect_type`` field. ``effect_type`` accepts both a
    :class:`ToolEffectType` enum and its string form so callers using
    either flavor get a consistent rewrite. Failure modes mirror the
    execution-mode setter: defensive shallow copy first, fall back to
    ``setattr`` only when the target is a regular (non-frozen) object,
    and never raise mid-normalization.
    """

    if isinstance(invocation, dict):
        updated = dict(invocation)
        updated["effect_type"] = effect_type
        return updated
    if isinstance(invocation, Mapping):
        updated = dict(invocation)
        updated["effect_type"] = effect_type
        return updated
    to_dict = getattr(invocation, "to_dict", None)
    if callable(to_dict):
        try:
            payload = to_dict()
        except (RuntimeError, TypeError, ValueError):
            return invocation
        if isinstance(payload, dict):
            updated = dict(payload)
            updated["effect_type"] = effect_type
            return updated
    if hasattr(invocation, "effect_type"):
        try:
            object.__setattr__(invocation, "effect_type", effect_type) if hasattr(
                invocation, "__frozen__"
            ) else setattr(invocation, "effect_type", effect_type)
        except (AttributeError, TypeError):
            return invocation
        return invocation
    return invocation


def normalize_replay_execution_modes(invocations: list[Any]) -> list[Any]:
    """Normalize ``execution_mode`` on replay-bound invocations (WS1 hot-fix).

    ``ToolBatchExecutor.execute_tool_batch`` buckets ``replay_invocations`` by
    ``execution_mode`` before handing them to ``ToolBatchRuntime``. When a
    decoded/native tool call already passed allow-list, guard, and mutation
    checks but was emitted without an ``execution_mode`` annotation, every
    bucket filter rejects it and the executor produces an empty
    ``ToolBatch``. The downstream hard gate then raises
    ``tool_dispatch_dropped: decoded tool batch produced no authoritative
    batch receipt``, which violates the ToolCallEnvelope normalization
    principle (every decoded call must resolve to one executable bucket).

    When a call is missing ``effect_type`` alongside the missing mode,
    ``ToolBatch``'s pydantic model validator still rejects the dispatch
    (the field is required for ``ToolInvocation``). The same canonical
    helper :py:func:`_infer_effect_type` that the ``ToolInvocation``
    model validator calls is reused here to fill it, so the dispatch
    shape stays in lockstep with the kernel's strict schema.

    Both fields are derived through the kernel's single tool-classification
    truth source (``ToolBatchRuntime.classify_tool`` for execution_mode and
    ``_infer_effect_type`` for effect_type), so we don't grow a parallel
    classification table here. Already-annotated invocations keep their
    original routing — only invocations whose ``execution_mode`` is
    missing, blank, or of an unrecognized shape are rewritten.

    Time complexity: O(n) over ``invocations``; each call performs one
    ``execution_mode`` read, one optional ``classify_tool`` call, one
    optional ``effect_type`` derivation, and at most one shallow copy
    per missing-mode invocation.

    Args:
        invocations: Replay-bound invocations to normalize in place-shape.

    Returns:
        A new list of invocations whose ``execution_mode`` (and
        ``effect_type``, when also missing) is set to a valid
        :class:`ToolExecutionMode` / :class:`ToolEffectType` value;
        original invocations with already-valid modes are passed through
        unchanged.

    Side Effects:
        None. The function does not mutate the input list; invocations
        without a valid mode are replaced with shallow copies that carry
        the synthesized mode/effect-type. The downstream
        ``tool_dispatch_dropped`` hard gate remains untouched — if the
        runtime still produces no authoritative receipt after
        normalization, the executor stays fail-closed.
    """

    if not invocations:
        return invocations
    normalized: list[Any] = []
    missing_mode_count = 0
    missing_effect_count = 0
    for invocation in invocations:
        if isinstance(invocation, Mapping):
            raw_mode = invocation.get("execution_mode")
        else:
            raw_mode = getattr(invocation, "execution_mode", None)
        if _is_valid_execution_mode(raw_mode):
            normalized.append(invocation)
            continue
        resolved_mode = _resolve_missing_execution_mode(invocation)
        missing_mode_count += 1
        rewritten = _set_invocation_execution_mode(invocation, resolved_mode)
        if isinstance(rewritten, Mapping):
            raw_effect = rewritten.get("effect_type")
        else:
            raw_effect = getattr(rewritten, "effect_type", None)
        if raw_effect is None or (
            not isinstance(raw_effect, ToolEffectType)
            and not (isinstance(raw_effect, str) and raw_effect in _VALID_TOOL_EFFECT_TYPES)
        ):
            resolved_effect = _infer_effect_type(extract_invocation_tool_name(rewritten), resolved_mode)
            missing_effect_count += 1
            rewritten = _set_invocation_effect_type(rewritten, resolved_effect)
        normalized.append(rewritten)
    if missing_mode_count or missing_effect_count:
        logger.debug(
            "tool_batch_execution_mode_normalized: filled_mode=%d filled_effect=%d total=%d",
            missing_mode_count,
            missing_effect_count,
            len(invocations),
        )
    return normalized


def _canonical_single_target_file(target_files: tuple[str, ...] | list[str]) -> str | None:
    normalized: dict[str, str] = {}
    for raw in target_files:
        token = str(raw or "").strip().replace("\\", "/")
        if not token:
            continue
        while token.startswith("./"):
            token = token[2:]
        if (
            not token
            or token.startswith("/")
            or token.startswith("~")
            or any(part == ".." for part in token.split("/"))
            or any(ch in token for ch in ("*", "?", "[", "]", ",", " ", "\t", "\n"))
        ):
            return None
        normalized.setdefault(token.lower(), token)
    if len(normalized) != 1:
        return None
    return next(iter(normalized.values()))


def _invocation_arguments(invocation: Any) -> dict[str, Any] | None:
    arguments: Any
    if isinstance(invocation, Mapping) or hasattr(invocation, "get"):
        arguments = invocation.get("arguments")
    else:
        arguments = getattr(invocation, "arguments", None)
    return arguments if isinstance(arguments, dict) else None


def _with_invocation_arguments(invocation: Any, arguments: dict[str, Any]) -> Any:
    if isinstance(invocation, dict):
        new_invocation = dict(invocation)
        new_invocation["arguments"] = arguments
        return new_invocation
    model_copy = getattr(invocation, "model_copy", None)
    if callable(model_copy):
        return model_copy(update={"arguments": arguments})
    to_dict = getattr(invocation, "to_dict", None)
    if callable(to_dict):
        new_invocation = dict(to_dict())
        new_invocation["arguments"] = arguments
        return new_invocation
    if isinstance(invocation, Mapping):
        new_invocation = dict(invocation)
        new_invocation["arguments"] = arguments
        return new_invocation
    return invocation


def fill_single_target_line_range_edit_blocks(
    invocations: list[Any],
    *,
    target_files: tuple[str, ...] | list[str],
) -> list[Any]:
    """Fill omitted ``file`` for unambiguous single-target edit_blocks calls.

    Weak models sometimes satisfy the narrowed line-range schema with
    ``start``/``end``/``replace`` but omit ``file``. Only repair this when the
    task contract declares exactly one safe target file; multi-target steps stay
    fail-closed.
    """

    target_file = _canonical_single_target_file(target_files)
    if not target_file:
        return invocations
    filled: list[Any] = []
    for invocation in invocations:
        tool_name = extract_invocation_tool_name(invocation)
        arguments = _invocation_arguments(invocation)
        if tool_name != "edit_blocks" or arguments is None:
            filled.append(invocation)
            continue
        has_file = any(str(arguments.get(key) or "").strip() for key in _FILE_ARGUMENT_KEYS)
        has_line_range = arguments.get("start") is not None and arguments.get("end") is not None
        has_replacement = any(str(arguments.get(key) or "").strip() for key in _LINE_RANGE_REPLACEMENT_KEYS)
        if has_file or not (has_line_range and has_replacement):
            filled.append(invocation)
            continue
        new_arguments = dict(arguments)
        new_arguments["file"] = target_file
        filled.append(_with_invocation_arguments(invocation, new_arguments))
    return filled


def _safe_contract_target_files(target_files: tuple[str, ...] | list[str]) -> list[str]:
    normalized: dict[str, str] = {}
    for raw in target_files:
        token = str(raw or "").strip().replace("\\", "/")
        if not token:
            continue
        while token.startswith("./"):
            token = token[2:]
        if (
            not token
            or token.startswith("/")
            or token.startswith("~")
            or any(part == ".." for part in token.split("/"))
            or any(ch in token for ch in ("*", "?", "[", "]", ",", "\t", "\n"))
        ):
            continue
        normalized.setdefault(token.lower(), token)
    return list(normalized.values())


WRITE_FILE_AUTOFILL_EVIDENCE_KEY = "write_file_target_autofilled"
WRITE_FILE_DUPLICATE_REJECTION_KEY = "write_file_duplicate_content_rejection"
_WRITE_FILE_AUTOFILL_BASIS = "sole_remaining_contract_target"


def _normalize_write_content_for_duplicate_check(content: str) -> str:
    """Normalize write_file content for duplicate detection (trivial whitespace only).

    Unifies line endings, strips per-line trailing whitespace and outer blank
    space so a retry that only differs in insignificant whitespace still counts
    as the same content.
    """

    unified = content.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in unified.split("\n")).strip()


def _with_invocation_top_level_field(invocation: Any, key: str, value: Any) -> Any:
    """Return a copy of the invocation with a top-level metadata field attached.

    Mirrors `_with_invocation_arguments`. Falls back to returning the original
    invocation unchanged when it cannot be safely copied; callers must treat the
    field as best-effort metadata, never as a dispatch precondition.
    """

    if isinstance(invocation, dict):
        new_invocation = dict(invocation)
        new_invocation[key] = value
        return new_invocation
    to_dict = getattr(invocation, "to_dict", None)
    if callable(to_dict):
        new_invocation = dict(to_dict())
        new_invocation[key] = value
        return new_invocation
    if isinstance(invocation, Mapping):
        new_invocation = dict(invocation)
        new_invocation[key] = value
        return new_invocation
    return invocation


def fill_content_only_write_file_from_remaining_targets(
    invocations: list[Any],
    *,
    target_files: tuple[str, ...] | list[str],
) -> list[Any]:
    """Fill omitted ``file`` for unambiguous content-only ``write_file`` calls.

    This is deliberately narrower than schema normalization: it needs the full
    batch order plus the structured task target list. If earlier calls in the
    same batch already claim all but one target and a later write_file has a
    complete body but no path, assign the sole remaining target. Multi-target
    ambiguity stays fail-closed.

    Fail-closed duplicate guard: when the file-less call's content equals (after
    trivial whitespace normalization) the content of an earlier same-batch write
    that already claimed a target, the call is a model retry/duplicate — NOT a
    request for the remaining target. Guessing there silently corrupts the
    remaining file, so the invocation is marked with
    ``WRITE_FILE_DUPLICATE_REJECTION_KEY`` instead of being filled; the executor
    converts that marker into a structured teaching error without dispatching
    any write (see ``split_write_file_duplicate_content_rejections``).

    Auditable autofill evidence is derived by the executor via
    ``diff_write_file_autofill_evidence`` (``ToolInvocation`` is a strict
    schema, so the evidence cannot ride on the invocation itself) and attached
    to the matching receipt result items so downstream lifecycle/audit can see
    the ``file`` argument was inferred, not model-provided.
    """

    contract_targets = _safe_contract_target_files(target_files)
    if not contract_targets:
        return invocations
    claimed: set[str] = set()
    claimed_write_contents: dict[str, str] = {}
    filled: list[Any] = []
    for invocation in invocations:
        tool_name = extract_invocation_tool_name(invocation)
        arguments = _invocation_arguments(invocation)
        if tool_name != "write_file" or arguments is None:
            filled.append(invocation)
            continue
        existing_file = extract_target_file_from_invocation_args(invocation)
        if existing_file:
            normalized_existing = existing_file.strip().replace("\\", "/")
            while normalized_existing.startswith("./"):
                normalized_existing = normalized_existing[2:]
            claimed.add(normalized_existing.lower())
            content_value = arguments.get("content")
            if isinstance(content_value, str):
                claimed_write_contents.setdefault(
                    _normalize_write_content_for_duplicate_check(content_value),
                    normalized_existing,
                )
            filled.append(invocation)
            continue
        if "content" not in arguments or not isinstance(arguments.get("content"), str):
            filled.append(invocation)
            continue
        normalized_content = _normalize_write_content_for_duplicate_check(str(arguments["content"]))
        duplicate_of = claimed_write_contents.get(normalized_content)
        if duplicate_of is not None:
            filled.append(
                _with_invocation_top_level_field(
                    invocation,
                    WRITE_FILE_DUPLICATE_REJECTION_KEY,
                    {
                        "duplicate_of": duplicate_of,
                        "reason": "duplicate_content_write_file_without_file_argument",
                    },
                )
            )
            continue
        remaining = [target for target in contract_targets if target.lower() not in claimed]
        if len(remaining) != 1:
            filled.append(invocation)
            continue
        new_arguments = dict(arguments)
        new_arguments["file"] = remaining[0]
        claimed.add(remaining[0].lower())
        claimed_write_contents.setdefault(normalized_content, remaining[0])
        filled.append(_with_invocation_arguments(invocation, new_arguments))
    return filled


def split_write_file_duplicate_content_rejections(
    invocations: list[Any],
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Separate duplicate-content rejected write_file calls from dispatchable ones.

    Each rejected invocation (marked by
    ``fill_content_only_write_file_from_remaining_targets``) becomes a
    structured error tool-result — a teaching error the model sees for the
    rejected call_id. The duplicate is never written anywhere and the original
    same-batch write keeps its own receipt.
    """

    dispatchable: list[Any] = []
    rejections: list[dict[str, Any]] = []
    for invocation in invocations:
        rejection: Any = None
        if isinstance(invocation, Mapping) or hasattr(invocation, "get"):
            rejection = invocation.get(WRITE_FILE_DUPLICATE_REJECTION_KEY)
        if not isinstance(rejection, Mapping):
            dispatchable.append(invocation)
            continue
        call_id = str(invocation.get("call_id", "") or "")
        duplicate_of = str(rejection.get("duplicate_of") or "")
        rejections.append(
            {
                "call_id": call_id,
                "tool_name": extract_invocation_tool_name(invocation) or "write_file",
                "status": "error",
                "result": None,
                "error": (
                    "duplicate_content_write_rejected: this write_file call omitted the 'file' "
                    f"argument and its content duplicates the earlier same-batch write to '{duplicate_of}'. "
                    "Nothing was written for this call. If you meant a different file, re-emit "
                    "write_file with an explicit 'file' argument and that file's own content."
                ),
                "execution_time_ms": 0,
                "effect_receipt": None,
                WRITE_FILE_DUPLICATE_REJECTION_KEY: {
                    "duplicate_of": duplicate_of,
                    "reason": str(rejection.get("reason") or "duplicate_content_write_file_without_file_argument"),
                },
            }
        )
    return dispatchable, rejections


def _invocation_call_id(invocation: Any) -> str:
    if isinstance(invocation, Mapping) or hasattr(invocation, "get"):
        return str(invocation.get("call_id", "") or "")
    return str(getattr(invocation, "call_id", "") or "")


def diff_write_file_autofill_evidence(
    invocations_before: list[Any],
    invocations_after: list[Any],
) -> dict[str, dict[str, Any]]:
    """Map call_id -> autofill evidence for write_file targets inferred by the fill pass.

    ``ToolInvocation`` is a strict schema (extra fields are forbidden), so the
    evidence cannot ride on the invocation itself. It is derived by diffing the
    batch before/after ``fill_content_only_write_file_from_remaining_targets``
    and later attached to the matching receipt result items (see
    ``annotate_autofilled_write_receipts``) so downstream lifecycle/audit can
    see the ``file`` argument was inferred, not model-provided.
    """

    fileless_before: set[str] = set()
    for invocation in invocations_before:
        if extract_invocation_tool_name(invocation) != "write_file":
            continue
        if extract_target_file_from_invocation_args(invocation):
            continue
        call_id = _invocation_call_id(invocation)
        if call_id:
            fileless_before.add(call_id)
    if not fileless_before:
        return {}
    evidence_by_call_id: dict[str, dict[str, Any]] = {}
    for invocation in invocations_after:
        if extract_invocation_tool_name(invocation) != "write_file":
            continue
        call_id = _invocation_call_id(invocation)
        if not call_id or call_id not in fileless_before:
            continue
        assigned_path = extract_target_file_from_invocation_args(invocation)
        if not assigned_path:
            continue
        evidence_by_call_id[call_id] = {
            "assigned_path": assigned_path,
            "basis": _WRITE_FILE_AUTOFILL_BASIS,
        }
    return evidence_by_call_id


def annotate_autofilled_write_receipts(
    receipts: list[dict[str, Any]],
    evidence_by_call_id: Mapping[str, dict[str, Any]],
) -> None:
    """Attach autofill evidence onto matching receipt result items (audit trail)."""

    if not evidence_by_call_id:
        return
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        for results_key in ("results", "raw_results"):
            result_items = receipt.get(results_key)
            if not isinstance(result_items, list):
                continue
            for item in result_items:
                if not isinstance(item, dict):
                    continue
                evidence = evidence_by_call_id.get(str(item.get("call_id") or ""))
                if evidence is not None:
                    item[WRITE_FILE_AUTOFILL_EVIDENCE_KEY] = dict(evidence)


def _normalize_file_reference_path(raw_path: str) -> str:
    """规范化工具调用中的文件路径字符串。

    处理 Windows 常见的混合格式：
    - file:// URI
    - 反斜杠分隔符
    - 误带前导斜杠的绝对盘符路径，例如 /C:/workspace/file.txt
    """

    normalized = str(raw_path or "").strip().replace("\\", "/")
    if not normalized:
        return ""
    if normalized.startswith("file://"):
        normalized = normalized[len("file://") :].lstrip("/")
    if len(normalized) >= 4 and normalized[0] == "/" and normalized[2:4] == ":/" and normalized[1].isalpha():
        normalized = normalized[1:]
    return normalized


_MUTATION_PATH_ARGUMENT_KEYS: tuple[str, ...] = (
    "path",
    "file_path",
    "target",
    "filename",
    "file",
    "filepath",
)


def _mutation_target_path_key(invocation: ToolInvocation) -> str | None:
    """Return a collapse key for file mutations, or None for pathless tools."""

    arguments = invocation.arguments if isinstance(getattr(invocation, "arguments", None), dict) else {}
    for key in _MUTATION_PATH_ARGUMENT_KEYS:
        raw = arguments.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        normalized = _normalize_file_reference_path(raw).lstrip("./")
        if normalized:
            return normalized
    return None


def _collapse_last_write_wins_mutations(
    mutations: list[ToolInvocation],
) -> tuple[list[ToolInvocation], list[tuple[str, str, str]]]:
    """Keep the last mutation per target path; soft-drop superseded earlier writes.

    R192/M03: L1-01 r12 sealed a 12-member write batch, committed 11 receipts,
    then denied the Nth claim with ``deo_current_policy_evidence_unavailable``
    because ``policy_target`` no longer matched prepare-time baseline content
    after an earlier same-path member already wrote the file. Last-write-wins
    preserves model intent while keeping DEO claim baselines coherent.
    """

    last_index_by_path: dict[str, int] = {}
    pathless_indexes: list[int] = []
    for index, invocation in enumerate(mutations):
        path_key = _mutation_target_path_key(invocation)
        if path_key is None:
            pathless_indexes.append(index)
        else:
            last_index_by_path[path_key] = index
    keep_indexes = set(last_index_by_path.values()) | set(pathless_indexes)
    collapsed: list[ToolInvocation] = []
    dropped: list[tuple[str, str, str]] = []
    for index, invocation in enumerate(mutations):
        if index in keep_indexes:
            collapsed.append(invocation)
            continue
        dropped.append(
            (
                str(invocation.call_id or ""),
                str(invocation.tool_name or invocation.raw_tool_name or "unknown_tool"),
                "deo_same_path_superseded_by_later_write",
            )
        )
    if dropped:
        logger.info(
            "DEO last-write-wins collapsed %s superseded same-path mutation(s); kept=%s",
            len(dropped),
            len(collapsed),
        )
    return collapsed, dropped


def _is_path_within_workspace(*, workspace_real: str, candidate_real: str) -> bool:
    try:
        return os.path.commonpath([workspace_real, candidate_real]) == workspace_real
    except ValueError:
        return False


def _resolve_existing_workspace_file(*, workspace: str, raw_path: str) -> str | None:
    normalized = _normalize_file_reference_path(raw_path)
    if not normalized:
        return None

    workspace_real = os.path.realpath(workspace or ".")
    if os.path.isabs(normalized):
        full_path = os.path.realpath(normalized)
    else:
        full_path = os.path.realpath(os.path.join(workspace_real, normalized))

    # 防御目录遍历：解析后的路径必须在 workspace 内部
    if not _is_path_within_workspace(workspace_real=workspace_real, candidate_real=full_path):
        logger.warning("Path traversal attempt blocked: %s", raw_path)
        return None

    if not os.path.isfile(full_path):
        return None

    # 返回相对于 workspace 的标准化路径，保持与原接口一致
    rel = os.path.relpath(full_path, workspace_real).replace("\\", "/")
    return rel


def rewrite_existing_file_paths_in_invocations(
    *,
    turn_id: str,
    workspace: str,
    invocations: list[Any],
) -> list[Any]:
    """将 invocation 中的文件路径重写为 workspace 内实际存在的路径。"""
    rewritten: list[Any] = []
    for invocation in invocations:
        tool_name = extract_invocation_tool_name(invocation)
        if not _tool_requires_existing_file(tool_name):
            rewritten.append(invocation)
            continue
        if isinstance(invocation, Mapping):
            raw_arguments = invocation.get("arguments")
        else:
            raw_arguments = getattr(invocation, "arguments", None)
        arguments = dict(raw_arguments) if isinstance(raw_arguments, Mapping) else {}
        if not arguments:
            rewritten.append(invocation)
            continue
        rewritten_invocation = invocation
        for path_key in ("file", "path", "filepath", "target"):
            raw_path = arguments.get(path_key)
            if not isinstance(raw_path, str):
                continue
            normalized_raw_path = raw_path.strip().replace("\\", "/")
            if not normalized_raw_path:
                continue
            resolved_path = _resolve_existing_workspace_file(workspace=workspace, raw_path=normalized_raw_path)
            if not resolved_path or resolved_path == normalized_raw_path:
                continue
            new_arguments = dict(arguments)
            new_arguments[path_key] = resolved_path
            if isinstance(invocation, Mapping):
                updated = dict(invocation)
                updated["arguments"] = new_arguments
                rewritten_invocation = cast(Any, updated)
            else:
                rewritten_invocation = {
                    "call_id": str(getattr(invocation, "call_id", "") or ""),
                    "tool_name": tool_name,
                    "arguments": new_arguments,
                    "effect_type": getattr(invocation, "effect_type", None),
                    "execution_mode": getattr(invocation, "execution_mode", None),
                }
            logger.warning(
                "mutation-path-correction: turn_id=%s tool=%s rewrite %s -> %s",
                turn_id,
                tool_name,
                normalized_raw_path,
                resolved_path,
            )
            break
        rewritten.append(rewritten_invocation)
    return rewritten


# ---------------------------------------------------------------------------
# Receipt 辅助
# ---------------------------------------------------------------------------


def _merge_batch_receipts(receipts: list[Any]) -> dict[str, Any] | None:
    """Merge multiple per-tool receipts into a single canonical batch receipt."""
    return merge_batch_receipts(receipts)


def _capability_token_from_effect_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    direct = receipt.get("capability_token")
    if isinstance(direct, dict):
        return dict(direct)
    director_policy = receipt.get("director_policy")
    if isinstance(director_policy, dict):
        nested = director_policy.get("capability_token")
        if isinstance(nested, dict):
            return dict(nested)
    return {}


def _effect_receipts_from_batch_receipts(receipts: list[Any]) -> list[dict[str, Any]]:
    """Compatibility wrapper for Run Ledger-owned effect receipt extraction."""
    return effect_receipts_from_batch_receipts(normalize_batch_receipts(receipts))


def _batch_result_count(receipts: list[Any]) -> int:
    count = 0
    for receipt in normalize_batch_receipts(receipts):
        results = receipt.get("results")
        if isinstance(results, list):
            count += sum(1 for item in results if isinstance(item, dict))
    return count


def _batch_has_authoritative_success(receipts: list[Any]) -> bool:
    """Return true when a batch contains at least one successful or pending effect.

    A decoded tool batch whose every result is ``status=error`` is not an
    executed turn, even though a batch receipt object exists. Treating that
    shape as success hides platform failures such as a cancelled TaskRuntime
    session rejecting all writes.
    """

    for receipt in normalize_batch_receipts(receipts):
        if int(receipt.get("pending_async_count") or 0) > 0 or bool(receipt.get("has_pending_async")):
            return True
        rows = receipt.get("results")
        if not isinstance(rows, list):
            rows = receipt.get("raw_results")
        normalized_rows = [item for item in rows or [] if isinstance(item, dict)]
        non_no_effect_rows = []
        for item in normalized_rows:
            result = item.get("result")
            no_effect = bool(item.get("no_op")) or (isinstance(result, dict) and bool(result.get("no_op")))
            if not no_effect:
                non_no_effect_rows.append(item)
        for item in non_no_effect_rows:
            if str(item.get("status") or "").strip().lower() == "success":
                return True
            effect_receipt = item.get("effect_receipt")
            if isinstance(effect_receipt, dict):
                return True
            result = item.get("result")
            if isinstance(result, dict) and isinstance(result.get("effect_receipt"), dict):
                return True
        top_level_effects = receipt.get("effect_receipts")
        if (
            isinstance(top_level_effects, list)
            and top_level_effects
            and (not normalized_rows or bool(non_no_effect_rows))
        ):
            return True
    return False


def _job_token_from_capability_token(token: dict[str, Any], *, run_id: str, stage: str) -> dict[str, Any]:
    audit_ok = token.get("capability_audit_ok")
    return {
        "schema_version": 1,
        "source": str(token.get("source") or "control_plane.job_token"),
        "token_id": str(token.get("token_id") or ""),
        "run_id": str(token.get("run_id") or run_id),
        "factory_run_id": str(token.get("factory_run_id") or token.get("run_id") or run_id),
        "project_id": str(token.get("project_id") or ""),
        "stage": str(token.get("stage") or stage or "tool_batch"),
        "contract_hash": str(token.get("contract_hash") or ""),
        "blueprint_hash": str(token.get("blueprint_hash") or ""),
        "execution_envelope_hash": str(token.get("execution_envelope_hash") or ""),
        "capability_audit": {
            "ok": bool(audit_ok) if audit_ok is not None else True,
            "issues": [],
        },
        "gate_policy": {
            "enabled_evidence_modalities": ["tool_receipt"],
            "required_evidence_modalities": [],
        },
    }


def _mapping_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            payload = to_dict()
        except (RuntimeError, TypeError, ValueError):
            return {}
        return dict(payload) if isinstance(payload, dict) else {}
    return {}


def _normalize_capability_token(value: dict[str, Any]) -> dict[str, Any]:
    token_id = str(value.get("token_id") or "").strip()
    if not token_id:
        return {}
    capability_audit = value.get("capability_audit")
    capability_audit_map = capability_audit if isinstance(capability_audit, dict) else {}
    raw_scope_value = (
        value.get("allowed_scope")
        or value.get("allowed_write_paths")
        or value.get("authorized_write_paths")
        or value.get("target_files")
        or []
    )
    raw_scope = [raw_scope_value] if isinstance(raw_scope_value, str) else list(raw_scope_value or [])
    allowed_scope = [str(item).replace("\\", "/").strip("/") for item in raw_scope if str(item).strip()]
    return {
        "source": str(value.get("source") or "control_plane.job_token"),
        "token_id": token_id,
        "run_id": str(value.get("run_id") or ""),
        "factory_run_id": str(value.get("factory_run_id") or value.get("run_id") or ""),
        "project_id": str(value.get("project_id") or ""),
        "stage": str(value.get("stage") or ""),
        "contract_hash": str(value.get("contract_hash") or ""),
        "blueprint_hash": str(value.get("blueprint_hash") or ""),
        "execution_envelope_hash": str(value.get("execution_envelope_hash") or ""),
        "capability_audit_ok": bool(value.get("capability_audit_ok", capability_audit_map.get("ok", True))),
        "allowed_scope": list(dict.fromkeys(item for item in allowed_scope if item)),
    }


def _execution_envelope_hash_from_metadata(metadata: Mapping[str, Any]) -> str:
    direct = str(metadata.get("execution_envelope_hash") or "").strip()
    if direct:
        return direct
    for key in ("task_execution_envelope", "director_execution_envelope", "execution_envelope"):
        envelope = _mapping_value(metadata.get(key))
        envelope_hash = str(envelope.get("envelope_hash") or "").strip()
        if envelope_hash:
            return envelope_hash
    return ""


def _capability_token_from_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("job_token", "control_plane_job_token", "capability_token"):
        token = _normalize_capability_token(_mapping_value(metadata.get(key)))
        if token:
            return token
    return _normalize_capability_token(dict(metadata))


def _append_tool_batch_receipts_to_run_ledger(
    *,
    workspace: str,
    run_id: str,
    role_id: str,
    task_id: str,
    turn_id: str,
    invocations: list[Any] | None,
    receipts: list[dict],
    capability_token: dict[str, Any] | None = None,
    execution_envelope_hash: str = "",
    provider_response_hash: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> None:
    decoded_count = len(invocations or [])
    merged_receipt = _merge_batch_receipts(receipts)
    effect_receipts = _effect_receipts_from_batch_receipts(receipts) if merged_receipt else []
    # Request-bound authority is immutable. Effect receipts may project it for
    # audit, but must never override the JobToken committed before the LLM call.
    token = _normalize_capability_token(capability_token or {})
    if not token:
        token = next(
            (
                candidate
                for receipt in effect_receipts
                if (candidate := _capability_token_from_effect_receipt(receipt)).get("token_id")
            ),
            {},
        )
    envelope_hash = str(execution_envelope_hash or token.get("execution_envelope_hash") or "").strip()
    if envelope_hash and token and not token.get("execution_envelope_hash"):
        token["execution_envelope_hash"] = envelope_hash
    stage = str(token.get("stage") or "tool_batch").strip() or "tool_batch"
    lifecycle = build_tool_batch_lifecycle_receipt_from_sources(
        run_id=str(run_id or ""),
        task_id=task_id,
        turn_id=turn_id,
        role=role_id,
        provider_response_hash=provider_response_hash,
        metadata=metadata,
        decoded_tool_calls_count=decoded_count,
        receipts=receipts,
        missing_receipt_reason="decoded_tool_batch_produced_no_authoritative_batch_receipt",
    )
    # Run Ledger identity must come from transaction authority. A turn id is
    # not a run id and must never be promoted into one merely to force a
    # projection write; legacy/unit callers without bound run authority stay
    # in-memory instead of polluting the process cwd's platform ledger.
    resolved_lifecycle_run_id = str(run_id or lifecycle.run_id or "").strip()
    if resolved_lifecycle_run_id:
        job_token = None
        if token:
            job_token = _job_token_from_capability_token(
                token,
                run_id=resolved_lifecycle_run_id,
                stage=stage,
            )
        append_tool_call_lifecycle_event(
            AppendToolCallLifecycleEventCommandV1(
                workspace=workspace,
                run_id=resolved_lifecycle_run_id,
                task_id=task_id,
                turn_id=turn_id,
                role=role_id,
                lifecycle_receipt=lifecycle.to_dict(),
                stage="tool_batch",
                project_id=task_id,
                job_token=job_token,
            )
        )
    if not merged_receipt:
        return
    failure_count = int(merged_receipt.get("failure_count") or 0)
    pending_async_count = int(merged_receipt.get("pending_async_count") or 0)
    ok = bool(lifecycle.ok) and failure_count == 0 and pending_async_count == 0
    if not effect_receipts and ok:
        return
    if not token:
        return

    resolved_run_id = str(token.get("run_id") or run_id or "").strip()
    if not resolved_run_id:
        return
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=workspace,
            run_id=resolved_run_id,
            event={
                "event_type": "gate_evaluated",
                "stage": stage,
                "gate": {
                    "name": "tool_receipt",
                    "ok": ok,
                    "summary": "token-scoped tool batch recorded" if ok else "token-scoped tool batch failed",
                },
                "job_token": _job_token_from_capability_token(token, run_id=resolved_run_id, stage=stage),
                "physical_evidence": {
                    "batch_receipt": merged_receipt,
                    "tool_receipts": effect_receipts,
                    "execution_envelope_hash": envelope_hash,
                    "command_count": 0,
                    "sampled_command_count": 0,
                    "commands_truncated": False,
                    "metadata": {
                        "role": role_id,
                        "task_id": task_id,
                        "turn_id": turn_id,
                    },
                },
            },
        )
    )


# ---------------------------------------------------------------------------
# ToolBatchExecutor
# ---------------------------------------------------------------------------
