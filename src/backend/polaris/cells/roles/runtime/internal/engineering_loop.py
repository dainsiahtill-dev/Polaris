"""One native-tool loop for each professional role; no model-maintained protocol."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import http.client
import json
import os
import re
import time
import traceback
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, cast

from polaris.cells.roles.runtime.internal.engineering_acceptance import ClauseFinding, require_observed_clause_evidence
from polaris.cells.roles.runtime.internal.engineering_execution_policy import resolve_execution_policy
from polaris.cells.roles.runtime.internal.engineering_file_cards import stale_file_observation
from polaris.cells.roles.runtime.internal.engineering_roles import register_consult_tool, role_instruction, role_scope
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools, bounded_output
from polaris.cells.roles.runtime.internal.engineering_validation_fingerprint import active_failure_revisions
from polaris.cells.roles.runtime.internal.engineering_verification import command_may_write
from polaris.cells.roles.runtime.internal.engineering_visual import build_visual_messages, require_current_visual_input
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    PermissionScope,
    RoleRunCommand,
    RoleRunResult,
    RunLimits,
    ToolObservation,
    tool_observation_payload,
)
from polaris.kernelone.llm.contracts.tool import ToolCall
from polaris.kernelone.llm.engine.client import ResilientLLMClient
from polaris.kernelone.llm.engine.contracts import AIRequest, AIResponse, TaskType
from polaris.kernelone.llm.engine.invocation_budget import invoke_with_budget
from polaris.kernelone.llm.toolkit.parsers.core import parse_tool_calls
from polaris.kernelone.process.process_tree import ProcessTreeDrainError

# Only a provider that did not answer at all is recoverable by the same
# role. An explicit upstream error, such as a rate limit or a server error,
# is the provider's own answer: the platform retry already owns it, and a
# role-level retry would multiply the attempts.
_UNANSWERED_PROVIDER_CATEGORIES = frozenset({"timeout", "network_error"})


def _is_transport_failure(response: AIResponse) -> bool:
    """A provider that did not answer is recoverable by the same role.

    A semantic failure, such as an invalid response or a broken contract,
    is not recoverable: the same request would fail the same way again and
    must end the run instead of looping.
    """
    category = getattr(response.error_category, "value", None)
    return isinstance(category, str) and category in _UNANSWERED_PROVIDER_CATEGORIES


def _has_remaining_budget(limits: RunLimits) -> bool:
    """The run deadline still allows another attempt."""
    if limits.deadline is None:
        return True
    return time.time() < limits.deadline


# Stream-level transport interruptions can escape the provider client as raw
# http.client exceptions before it can classify them. They carry no provider
# answer and no tool effect, so the same role may retry its own turn.
_TRANSIENT_PROVIDER_EXCEPTIONS = (
    http.client.IncompleteRead,
    http.client.RemoteDisconnected,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
)


def _active_structured_repairs(
    events: list[dict[str, Any]], *, threshold: int = 2, limit: int = 3
) -> list[dict[str, Any]]:
    """Find symptoms that survived real re-tests on different candidates."""

    failures = active_failure_revisions(events)
    repairs: list[dict[str, Any]] = []
    for signature, revisions in failures.items():
        candidate_revisions = len(set(revisions) - {None})
        if len(revisions) >= threshold and candidate_revisions >= 2:
            repairs.append(
                {"signature": signature, "occurrences": len(revisions), "candidate_revisions": candidate_revisions}
            )
    repairs.sort(key=lambda repair: repair["occurrences"], reverse=True)
    return repairs[:limit]


def _structured_repair_message(repairs: list[dict[str, Any]]) -> dict[str, Any]:
    lines = [
        f"- {item['signature']}（已在 {item['candidate_revisions']} 个候选上复现 {item['occurrences']} 次）"
        for item in repairs
    ]
    return {
        "role": "system",
        "content": (
            "结构化修复已触发：以下症状在多个候选上真实复现，说明当前修复没有消除根因。\n"
            + "\n".join(lines)
            + "\n\n请一次只检验一个可证伪假设，并给出：观察（具体回执）、假设（最能解释它的原因）、"
            "区分方法（能把它与其他解释区分开的检查）、预测（假设成立时应观察到的变化）、动作（读代码/查环境/最小修改）、"
            "复测（支持、反驳还是证据不足）。动作可以是读取或环境检查，不必每轮改生产代码；"
            "允许为同一假设修改多个文件，但所有修改必须服务于该假设。"
        ),
    }


def _turn_had_action(events: Sequence[Mapping[str, Any]], turn_seq: int) -> bool:
    """Whether one logical turn did any real work.

    Reading the source and rendering the page *is* work. Counting only writes
    made the guard fire on legitimate exploration: measured across 68 real runs
    it intervened 406 times, including on turns that ended in repo_tree + repo_rg
    and on turns where the model had simply spent two turns thinking before it
    edited. A turn is idle only when it produced no successful tool call at all;
    re-reading the same file is a separate, already detected loop.
    """

    for event in events:
        if event.get("kind") != "tool_observed" or event["payload"].get("turn_seq") != turn_seq:
            continue
        if event["payload"].get("ok") is True:
            return True
    return False


def _stall_streak(events: Sequence[Mapping[str, Any]], turn_seqs: Sequence[int]) -> int:
    streak = 0
    for turn_seq in reversed(list(turn_seqs)):
        if _turn_had_action(events, turn_seq):
            break
        streak += 1
    return streak


def _repeated_read_paths(events: Sequence[Mapping[str, Any]], *, threshold: int = 3) -> list[str]:
    """Files the model keeps re-reading instead of acting on."""

    counts: dict[str, int] = {}
    for event in events:
        if event.get("kind") != "tool_observed" or event["payload"].get("tool") != "read_file":
            continue
        if event["payload"].get("ok") is not True:
            continue
        try:
            body = json.loads(str(event["payload"].get("content") or "{}"))
        except json.JSONDecodeError:
            continue
        card = body.get("file_card") if isinstance(body, dict) else None
        if isinstance(card, dict) and isinstance(card.get("path"), str):
            counts[card["path"]] = counts.get(card["path"], 0) + 1
    return sorted(path for path, count in counts.items() if count >= threshold)


def _stall_message(streak: int, paths: Sequence[str]) -> dict[str, Any]:
    target = "、".join(str(path) for path in paths[:4]) if paths else "已读文件"
    return {
        "role": "system",
        "content": (
            f"停滞检测：连续 {streak} 轮没有产生任何工具观察。"
            f"以下文件已被反复读取（{target}）。"
            "请停止重读，给出唯一最小动作：一句可检验的下一步（修改/验证/环境检查），"
            "并说明它如何推进目标；若证据已足够，直接执行。"
        ),
    }


def _provider_transport_message(error: object) -> dict[str, Any]:
    """A recoverable provider interruption that ran no tool from this turn."""

    return {
        "role": "user",
        "content": (
            "The provider request failed before any tool from this turn ran: "
            f"{error}. Earlier observed tool effects are "
            "unchanged and no candidate was accepted. Continue the original goal using the same "
            "available tools and permissions; do not repeat a rejected or already observed action."
        ),
    }


class NativeTurnRejectedError(ValueError):
    """Correctable model output, rejected before any candidate is dispatched."""

    def __init__(self, reason: str, calls: list[Any], terminal: Any) -> None:
        super().__init__(reason)
        self.diagnostic = {
            "reason": reason,
            "stop_reason": terminal,
            "tools_dispatched": False,
            "candidates": [
                {
                    "index": index,
                    "call_id": call.get("id") if isinstance(call, dict) else None,
                    "tool": (
                        call.get("function", {}).get("name")
                        if isinstance(call, dict) and isinstance(call.get("function"), dict)
                        else call.get("name")
                        if isinstance(call, dict)
                        else None
                    ),
                }
                for index, call in enumerate(calls)
            ],
        }


def _native_calls(response: AIResponse) -> list[ToolCall]:
    """Validate the complete native envelope before any tool can have effects."""
    raw = response.raw
    if not isinstance(raw, dict):
        raise ValueError("native provider response is missing its raw object")
    calls: Any = []
    terminal: Any = None
    if "choices" in raw:
        choices = raw["choices"]
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError("native provider response requires one choice")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("native provider response has no assistant message")
        calls = message.get("tool_calls", [])
        terminal = choice.get("finish_reason")
    elif "content" in raw or raw.get("stop_reason") == "max_tokens":
        terminal = raw.get("stop_reason")
        blocks = raw.get("content")
        if blocks is None and terminal == "max_tokens":
            # Some compatible providers omit an unfinished content block at
            # the output boundary. No call exists to dispatch or replay.
            raise NativeTurnRejectedError(f"native turn is incomplete: {terminal}", [], terminal)
        if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
            raise ValueError("native provider content must be an object list")
        calls = [block for block in blocks if block.get("type") == "tool_use"]
    else:
        # Provider-agnostic fallback. The two envelopes above carry explicit
        # terminal/call-id protocol facts; anything else is delegated to the
        # unified parser, which already covers Gemini (functionCall parts),
        # Ollama, Mistral, Groq, Cohere, Vertex and Bedrock. A newly added
        # provider must not require a new branch in the engineering loop.
        unified = parse_tool_calls(response=raw, provider="auto", allow_text_fallback=False)
        if not unified:
            raise ValueError("unsupported native provider response envelope")
        calls = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in unified
            if not call.parse_error
        ]
        terminal = "tool_calls"
    if not isinstance(calls, list):
        raise ValueError("native tool_calls must be a list")
    if terminal in {"tool_calls", "tool_use"} and not calls:
        raise ValueError("native provider announced tools without complete calls")
    if calls and terminal not in {"tool_calls", "tool_use", "stop", "end_turn", "length", "max_tokens"}:
        raise ValueError(f"native tool turn is incomplete: {terminal}")
    identities: set[str] = set()
    for call in calls:
        if not isinstance(call, dict):
            raise ValueError("native tool_calls contains a non-object entry")
        if call.get("type") not in {"function", "tool_use"}:
            raise ValueError("native tool_calls contains an unsupported envelope")
        function = call.get("function") if call["type"] == "function" else call
        if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"].strip():
            raise ValueError("native tool call is missing its function name or envelope")
        identity = call.get("id")
        if not isinstance(identity, str) or not identity.strip() or identity in identities:
            # Native identity is a protocol responsibility, not a field the
            # engineering model should repair or reinterpret.
            raise ValueError("native tool turn has a missing, invalid, or duplicate call id")
        identities.add(identity)
    if terminal in {"length", "max_tokens"}:
        raise NativeTurnRejectedError(f"native turn is incomplete: {terminal}", calls, terminal)
    parsed = parse_tool_calls(tool_calls=calls, allow_text_fallback=False)
    if len(parsed) != len(calls) or any(call.parse_error for call in parsed):
        detail = "; ".join(call.parse_error for call in parsed if call.parse_error)
        raise NativeTurnRejectedError(
            f"native tool turn rejected: {detail or 'incomplete parser result'}", calls, terminal
        )
    return cast("list[Any]", (parsed))


async def _invoke(
    client: ResilientLLMClient, request: AIRequest, cancel: asyncio.Event, deadline: float | None
) -> AIResponse:
    invocation = client.invoke(request)
    if deadline is not None:
        invocation = invoke_with_budget(invocation, max(0.001, deadline - time.time()))
    worker = asyncio.create_task(invocation)
    cancellation = asyncio.create_task(cancel.wait())
    try:
        timeout = max(0.0, deadline - time.time()) if deadline is not None else None
        done, _pending = await asyncio.wait(
            {worker, cancellation}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if cancellation in done or not done:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            if cancel.is_set():
                raise asyncio.CancelledError
            raise TimeoutError("engineering provider deadline exceeded")
        return await worker
    except asyncio.CancelledError:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        raise
    finally:
        cancellation.cancel()
        await asyncio.gather(cancellation, return_exceptions=True)


def _model_context_window(workspace: Path, role: str) -> int:
    """Resolve the context window of the model this role will use.

    The native tool loop has no budget of its own, so the working set has to
    know the window of the model that will receive it. The provider
    configuration is the same source the executor resolves the model from.
    """
    try:
        from polaris.kernelone.llm import config_store as llm_config
        from polaris.kernelone.llm.runtime_config import get_role_model

        provider_id, _model = get_role_model(role)
        cache_root = llm_config.resolve_workspace_cache_root_for_workspace(str(workspace))
        config = llm_config.load_llm_config(str(workspace), cache_root, settings=None)
        providers = config.get("providers")
        provider = providers.get(provider_id) if isinstance(providers, dict) else None
        window = int((provider or {}).get("max_context_tokens") or 0)
    except (AttributeError, OSError, TypeError, ValueError):
        return 0
    return window if window > 0 else 0


# A long native tool working set has no natural budget: left unchecked it grows
# until the provider window refuses the request, so a weak model spends its life
# inside a saturated context and keeps redoing finished work. The working set is
# therefore bounded by design. The tight budget is the intended operating point;
# the window-derived ceiling is only a fallback for a final turn that cannot fit
# the tight budget. The durable journal keeps every observation either way.
_WORKING_SET_TOKENS_ENV = "KERNELONE_ENGINEERING_WORKING_SET_TOKENS"
_WORKING_SET_TOKENS_DEFAULT = 256000
_WINDOW_RESERVE_TOKENS = 16384 + 8192

# Wrap-up gate: a weak model that passes verification and then keeps editing
# outputs invalidates its own passing evidence and forces an endless re-verify
# cycle, so the run never reaches independent QA. One change-then-reverify cycle
# is allowed; from the second pass -> write cycle onward, explicit source writes
# and shell commands are intercepted, and two blocked attempts end the Director
# frame so QA reviews the candidate that actually passed. Independent QA is the
# safety net for a genuinely premature finish.
_WRAP_UP_GATE_ENV = "KERNELONE_ENGINEERING_WRAP_UP_GATE"
_WRAP_UP_GATE_MAX_BLOCKS = 2
# Pass -> write cycles allowed before the wrap-up gate intercepts writes.
# 0 = freeze the candidate at the first durable pass and hand it to independent
# QA (only a QA rejection unfreezes it); 1 = allow one change-then-reverify
# cycle. Chosen by an A/B comparison on real runs, not by taste.
_WRAP_UP_CYCLES_ENV = "KERNELONE_ENGINEERING_WRAP_UP_CYCLES"


def _wrap_up_cycle_allowance() -> int:
    raw = os.environ.get(_WRAP_UP_CYCLES_ENV, "0")
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


# Independent QA is a review, not an open-ended exploration. A weak model can
# spend the whole shared run budget re-listing the workspace without ever
# submitting report_review (observed: 69 QA execute_commands, no report, run
# cancelled at budget). Bound the QA frame's turns: warn at the limit, hard
# finish shortly after so the run keeps an honest terminal state.
_FIRST_VERIFICATION_FRACTION_ENV = "KERNELONE_ENGINEERING_FIRST_VERIFICATION_FRACTION"
_FIRST_VERIFICATION_FRACTION_DEFAULT = 0.4
_QA_TURN_LIMIT_ENV = "KERNELONE_ENGINEERING_QA_MAX_TURNS"
_QA_TURN_LIMIT_DEFAULT = 50
_QA_TURN_GRACE = 5
# The Director has no hard stop of its own, so a slow run hands the reviewer
# whatever is left. Measured on L1-01: the Director took 49.5 of 90 minutes, the
# reviewer then spent 32 of its 40 minutes and was still mid-review ("I'll start
# by reading the actual source files") when the 600s reserve forced a verdict —
# the run ended interrupted with project_outcome unknown even though verification
# had passed. The reviewer gets a guaranteed share and the Director yields.
_QA_MIN_FRACTION_ENV = "KERNELONE_ENGINEERING_QA_MIN_FRACTION"
_QA_MIN_FRACTION_DEFAULT = 0.45


def _retention_fold_window(
    fold_window: int | None,
    *,
    history_tokens: int,
    context_window_tokens: int,
) -> int | None:
    """Narrow the fold window when the retained working set cannot hold the history.

    The working-set cap drops whole turns by token budget while the fold window
    is activity-based, so history can be discarded before the distiller ever sees
    it. Folding harder first makes distillation the compression mechanism and
    dropping turns the last resort.
    """

    if not fold_window:
        return fold_window
    allowance = _history_allowances(context_window_tokens)[0]
    window = fold_window
    # Bounded: at most two halvings, never below four activities, so the current
    # turn and its immediate context are never folded away.
    for _ in range(2):
        if history_tokens <= allowance or window <= 4:
            break
        window = max(4, window // 2)
    return window


def _history_allowances(window: int) -> list[int]:
    """Ascending candidate budgets for the retained native history."""
    window_allowance = int(window * 0.95) - _WINDOW_RESERVE_TOKENS
    if window_allowance <= 0:
        return [max(2048, int(window * 0.5))]
    configured = int(os.environ.get(_WORKING_SET_TOKENS_ENV, str(_WORKING_SET_TOKENS_DEFAULT)) or 0)
    tight = min(configured, window_allowance) if configured > 0 else window_allowance
    return sorted({tight, window_allowance})


def _bounded_request_messages(
    messages: list[dict[str, Any]],
    *,
    store: EngineeringSessionStore,
    command: RoleRunCommand,
    role: str,
    context_window_tokens: int,
    visual_binding: Callable[[], dict[str, Any]] | None,
    audit: dict[str, Any],
    semantic_runtime: Any = None,
    fold_window: int | None = None,
    force_fold_paths: Sequence[str] = (),
    supplemental_text: str = "",
) -> list[dict[str, Any]]:
    """Build the request messages inside the model's context window.

    A native tool working set has no budget: it grows with every turn until
    the provider window refuses the whole request. The history is therefore
    bounded before the request is built, by dropping whole older turns. The
    durable journal keeps every observation, so nothing is lost.
    """
    from polaris.cells.roles.runtime.internal.engineering_tool_payload_projection import (
        fold_tool_payload_previews,
        folded_payload_projections,
        message_payload_tokens,
    )

    def _visual_aligned_cost(message: Mapping[str, Any]) -> int:
        # Shared with the retention check: base64 image bytes are priced as
        # binary, never as text (run 6e61f355 died counting them as text).
        return message_payload_tokens(message)

    window = int(context_window_tokens or 0)
    messages = fold_tool_payload_previews(
        messages,
        semantic_summaries=semantic_runtime.fold_summaries() if semantic_runtime is not None else None,
        inactive_fold_activities=fold_window,
        force_fold_paths=force_fold_paths,
    )
    prior_fold_refs = {
        str(event["payload"].get("projection_ref"))
        for event in store.load_events(command.session_id, kinds=("context_projection_folded",), role=role)
        if event["payload"].get("projection_ref")
    }
    for projection in folded_payload_projections(messages):
        reference = str(projection["projection_ref"])
        if reference in prior_fold_refs:
            continue
        store.append_event(command.session_id, kind="context_projection_folded", role=role, payload=projection)
        prior_fold_refs.add(reference)
    events = store.load_events(
        command.session_id,
        kinds=(
            "message",
            "tool_observed",
            "tool_started",
            "visual_observation_completed",
            "llm_request_observed",
        ),
        role=role,
    )

    def required_call_ids(selection_audit: dict[str, Any]) -> set[str]:
        return {
            item["call_id"]
            for item in selection_audit.get("image_sets", ())
            if isinstance(item.get("call_id"), str) and item.get("required") is not False
        }

    def select(text: str, selection_audit: dict[str, Any], encountered: set[str]) -> list[dict[str, Any]]:
        # The newest native tool group stays mandatory during trimming. Price the
        # request-local host data separately and project it onto the budgeted history.
        supplement = [{"role": "user", "content": text}] if text else []
        supplement_cost = sum(_visual_aligned_cost(message) for message in supplement)

        def build(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
            projected = build_visual_messages(
                [*history, *supplement],
                events=events,
                role=role,
                binding_provider=visual_binding,
                audit=selection_audit,
            )
            encountered.update(required_call_ids(selection_audit))
            # Pixel admission is bounded by the model window alone. There is no
            # separate working-set target for images: the old 32k allowance admitted
            # captures newest-first up to a token budget, which silently discarded
            # visual evidence the model had already been shown. Mandatory captures
            # (the current candidate, a pending reinspection, an explicit pin) are
            # only demoted when they cannot fit the window at all. Removed pixels
            # stay inspectable with browser_observe(revisit_call_ids=[...]).
            hard_allowance = _history_allowances(window)[-1] if window > 0 else 0
            if hard_allowance <= 0:
                return projected
            from polaris.kernelone.llm.engine.visual_working_set import select_visual_working_set

            selected, selection = select_visual_working_set(
                projected,
                selection_audit.get("image_sets", []),
                hard_allowance=hard_allowance,
                optional_allowance=hard_allowance,
            )
            selection_audit["visual_selection"] = selection
            return selected

        built: list[dict[str, Any]] | None = None
        try:
            built = build(messages)
        except ValueError as error:
            if "required_visual_input_budget_exceeded" not in str(error):
                raise
            # The history does not fit the window yet.  The text budget belongs
            # to the trimmer, so the raw history is trimmed first and only the
            # trimmed history is projected.  Projecting the raw history must not
            # end the run: a long conversation is a trimming question, not a
            # visual budget failure.
        if built is not None and window <= 0:
            return built
        from polaris.kernelone.llm.engine.visual_working_set import visual_payload_cost

        cost = visual_payload_cost(built) if built is not None else None
        allowances = _history_allowances(window)
        if built is not None and cost is not None and cost["admission_proxy_tokens"] <= allowances[0]:
            return built
        from polaris.kernelone.llm.engine.prompt_budget import compress_native_tool_history

        # The compression works on the raw history. The visual projection moves
        # the image bytes out of the tool content into attachment blocks, so
        # compressing an already projected history and projecting it again would
        # read image blocks without their bytes.
        # The text estimator and the admission estimator are not the same
        # tokenizer, so a single trim pass can still project over the allowance.
        # Retry with progressively smaller budgets instead of ending the run:
        # a long conversation is a trimming question, not a terminal failure.
        # With the aligned cost, group prices already include image bytes, so the
        # retry budget is the full allowance, not the text-only remainder.
        last_error: ValueError | None = None
        for allowance in allowances:
            for scale in (1.0, 0.75, 0.5, 0.3):
                attempt_budget = max(2048, int(allowance * scale))
                bounded = compress_native_tool_history(
                    messages, attempt_budget - supplement_cost, message_cost=_visual_aligned_cost
                )
                if bounded is None:
                    continue
                try:
                    return build(bounded)
                except ValueError as error:
                    message = str(error)
                    # A tighter working set must never drop required visual evidence.
                    # Fall back to the window-derived ceiling for this request
                    # instead of failing the run or losing pinned pixels.
                    if (
                        "required_visual_input_budget_exceeded" not in message
                        and "pinned visual input is unavailable" not in message
                    ):
                        raise
                    last_error = error
        if last_error is not None:
            raise last_error
        raise ValueError(
            "required_visual_input_budget_exceeded: the trimmed history still does not fit the model window"
        )

    def retained_required(selection_audit: dict[str, Any]) -> set[str]:
        selection = selection_audit.get("visual_selection", {})
        removed = set(selection.get("demoted_call_ids", ())) | set(selection.get("dropped_mandatory_call_ids", ()))
        return required_call_ids(selection_audit) - removed

    if not supplemental_text:
        return select("", audit, set())

    advised_audit: dict[str, Any] = {}
    encountered: set[str] = set()
    try:
        advised = select(supplemental_text, advised_audit, encountered)
    except ValueError as error:
        if not any(
            reason in str(error)
            for reason in ("required_visual_input_budget_exceeded", "pinned visual input is unavailable")
        ):
            raise
        # A supplemental budget failure says nothing about admission of the native turn itself.
        baseline = select("", audit, set())
        audit["supplemental_context_omitted"] = {"reason": "model_budget", "chars": len(supplemental_text)}
        return baseline

    retained = retained_required(advised_audit)
    if encountered - retained:
        baseline_audit: dict[str, Any] = {}
        baseline = select("", baseline_audit, set())
        restored = retained_required(baseline_audit) - retained
        if restored:
            audit.update(baseline_audit)
            audit["supplemental_context_omitted"] = {
                "reason": "preserve_required_captures",
                "chars": len(supplemental_text),
                "restored_call_ids": sorted(restored),
            }
            return baseline
    audit.update(advised_audit)
    return advised


_CONTINUATION_HISTORY_NOTE = (
    "The earlier messages above are this same run's earlier turns. Their tool observations describe the state at "
    "the time they ran, not the current state. Re-read the current files and run the current checks before you "
    "rely on any earlier observation, and do not restart work that those earlier turns already completed."
)

# Distill the FIRST actionable error out of a long verification log so a weak
# model fixes one concrete location instead of re-reading the whole suite.
_ERROR_LINE_PATTERNS = (
    re.compile(r"^.*?-->\s*([\w./-]+\.(?:rs|py|cpp|cc|c|h|hpp|js|ts|tsx|java|go)):(\d+):(\d+)", re.MULTILINE),
    re.compile(r"^.*?panicked at '([^']+):(\d+):(\d+)'", re.MULTILINE),
    re.compile(r"^(?:FAILED|FAIL)\b[^\n]*\[([^\]]+)\]\s*[:.]?\s*(.*)$", re.MULTILINE),
    re.compile(r"^.*?([\w./-]+\.(?:rs|cpp|cc|c|h|hpp|py|js|ts|tsx|java|go)):(\d+)(?::(\d+))?[:\s]+(.*)$", re.MULTILINE),
    re.compile(r"^(?:FAILED|FAIL|ERROR|error)\b[^\n]*\[[^\]]+\]\s*[:.]?\s*(.*)$", re.MULTILINE),
)


def _qa_source_read(event: Mapping[str, Any]) -> bool:
    payload = event["payload"]
    return (
        event["kind"] == "tool_observed"
        and event["role"] == "qa"
        and payload.get("ok") is True
        and not payload.get("interrupted")
        and not payload.get("timed_out")
        and (
            (payload.get("tool") == "read_file" and not stale_file_observation(payload))
            or (payload.get("tool") == "repo_rg" and bool(str(payload.get("content", "")).strip()))
        )
    )


def _loop_recovery_instruction(role: str, enabled: frozenset[str]) -> str:
    if role == "qa" and "report_review" in enabled:
        return (
            "Submit report_review using exact current observation call IDs for passing clauses. "
            "Record observed defects as fail and unavailable or inconclusive evidence as unknown. "
            "If an unresolved clause still needs a check, use a different authorized read-only observation. "
            "Budget the window you are given: a browser render costs tens of seconds while a source read "
            "costs one round trip, so read the source and run the host's declared checks first and spend "
            "renders only on the clauses that genuinely need pixels."
        )
    if role != "director":
        return "Return your advice from the observations gathered, identifying any unresolved questions."
    return (
        "Take the next authorized action toward the goal. Address a missing implementation or test, "
        "or verify the candidate with its declared checks. Read a specific file when needed; do not re-list the workspace."
    )


def _stage_gate_verification_hint(workspace: Path) -> str:
    """A concrete, project-typed verification invocation for the stage gate.

    A weak model complies with an exact call, not an abstract instruction.
    Detect the project type from the workspace and return a copy-paste action.
    """
    ws = Path(workspace)
    if (ws / "Cargo.toml").is_file():
        return "Run exactly: verify_project(commands=['cargo build && cargo test'])."
    if (ws / "Makefile").is_file():
        return "Run exactly: verify_project(commands=['make'])."
    if (ws / "package.json").is_file():
        return "Run exactly: verify_project(commands=['npm test'])."
    if (ws / "index.html").is_file():
        return "Run exactly: browser_observe(entry='index.html') and fix every runtime error it reports."
    return "Run verify_project with the build and test commands for this project."


def _distill_verification_failure(content: str) -> str:
    """Return a short first-error fix target from a failed verification log."""
    if not content:
        return ""
    if "read-only file system" in content or "EROFS" in content:
        from polaris.cells.roles.runtime.internal.engineering_verification import (
            read_only_write_targets,
        )

        targets = read_only_write_targets(content)
        target_note = f" to {', '.join(targets)}" if targets else ""
        return (
            "Verification runs with workspace inputs read-only: your check tried to write"
            f"{target_note}. Run the build or preparation with execute_command first (ordinary "
            "authorized tools can write the workspace), then verify the prepared candidate with a "
            "read-only command. Do not repeat the build inside verify_project."
        )
    for pattern in _ERROR_LINE_PATTERNS:
        match = pattern.search(content)
        if match:
            fragment = match.group(0).strip()
            return (
                "Distilled first failing evidence (fix this specific location before broader retesting):\n"
                + fragment[:500]
            )
    # Fall back to the first line that carries an error/failure marker.
    for line in content.splitlines():
        lowered = line.lower()
        if any(token in lowered for token in ("error", "failed", "fail", "panic", "assertion")):
            return (
                "Distilled first failing evidence (fix this specific location before broader retesting):\n"
                + line.strip()[:500]
            )
    return ""


async def run_engineering_loop(
    command: RoleRunCommand,
    *,
    scope: PermissionScope,
    limits: RunLimits,
    cancel: asyncio.Event,
    store: EngineeringSessionStore,
    client: ResilientLLMClient,
    consult: Callable[[str, str, int, str], Awaitable[str]] | None = None,
    verify: Callable[[tuple[str, ...], int, str], Awaitable[ToolObservation]] | None = None,
    verification_output_declarations: tuple[Path, ...] | None = None,
    observe: Callable[[dict[str, Any], int, str], Awaitable[ToolObservation]] | None = None,
    visual_binding: Callable[[], dict[str, Any]] | None = None,
    artifact_binding: Callable[[], dict[str, Any]] | None = None,
    authorize: Callable[[], None] | None = None,
    pending_ancestors: frozenset[tuple[int, str]] = frozenset(),
    project_run: bool = True,
    route_request: Callable[[AIRequest, int], tuple[ResilientLLMClient, dict[str, Any]]] | None = None,
    purpose: str = "role",
    review_clauses: tuple[str, ...] = (),
) -> RoleRunResult:
    """Run one role while the public service holds the workspace writer lock.

    Prepared requests below are auxiliary input evidence, not the final physical
    provider body. ContextOS transport-body closure is a separate integration.
    No controller, lock, or second journal is created here.
    """
    from polaris.cells.roles.runtime.internal.engineering_roles import (
        interpret_review_reports,
        qa_review_reserve_seconds,
        register_browser_observation_tool,
        register_review_tool,
        register_verification_tool,
    )

    register_consult_tool()
    register_review_tool()
    register_verification_tool()
    register_browser_observation_tool()
    scope = role_scope(command.role, scope)
    active_dispatch: tuple[int, str] | None = None

    async def consult_current(role: str, question: str) -> str:
        if consult is None or active_dispatch is None:
            raise ValueError("consultation requires an active tool dispatch")
        return await consult(role, question, *active_dispatch)

    async def verify_current(commands: tuple[str, ...]) -> ToolObservation:
        if verify is None or active_dispatch is None or command.role != "director":
            raise ValueError("verify_project requires an active Director dispatch")
        return await verify(commands, *active_dispatch)

    async def observe_current(arguments: dict[str, Any]) -> ToolObservation:
        if observe is None or active_dispatch is None:
            raise ValueError("browser_observe requires an active tool dispatch")
        return await observe(arguments, *active_dispatch)

    def check_visual() -> None:
        if scope.browser is not None and scope.browser.require_visual_review:
            if visual_binding is None or active_dispatch is None:
                raise ValueError("current visual candidate binding is unavailable")
            require_current_visual_input(
                store,
                command.session_id,
                role=command.role,
                frame_seq=frame_seq,
                turn_seq=active_dispatch[0],
                current_binding=visual_binding(),
            )

    def review_current(approved: bool, clause_findings: list[ClauseFinding]) -> None:
        if command.role != "qa" or active_dispatch is None:
            raise ValueError("report_review requires an active QA dispatch")
        events = store.load_events(command.session_id)
        if not any(frame_seq < event["seq"] < active_dispatch[0] and _qa_source_read(event) for event in events):
            raise ValueError("report_review requires a successful current-frame source read in a previous turn")
        if approved:
            if clause_findings:
                require_observed_clause_evidence(
                    clause_findings,
                    events=events,
                    checks=store.load_run(command.session_id, workspace=command.workspace)["checks"],
                    frame_seq=frame_seq,
                    report_turn_seq=active_dispatch[0],
                )
            check_visual()

    def resolve_file_card(reference: str, path: str | None) -> dict[str, Any]:
        from polaris.cells.roles.runtime.internal.engineering_file_cards import resolve_observed_file

        return resolve_observed_file(store.load_events(command.session_id, kinds=("tool_observed",)), reference, path)

    from polaris.cells.roles.runtime.internal.engineering_semantic_runtime import (
        SemanticRuntime,
        facts_for_advice,
        semantic_runtime_from_environment,
    )

    semantic_runtime: SemanticRuntime | None = None
    try:
        semantic_runtime = semantic_runtime_from_environment()
    except Exception as error:  # noqa: BLE001 - optional assist wiring must never terminate the run
        store.append_event(
            command.session_id,
            kind="run_diagnostic",
            role=command.role,
            payload={
                "area": "semantic_advisor_runtime",
                "error": f"{type(error).__name__}: {error}",
                "fatal": False,
            },
        )
        semantic_runtime = None
    if semantic_runtime is not None:
        try:
            from polaris.cells.roles.runtime.internal.engineering_semantic_advisor import (
                advisor_configs_from_workspace,
            )
            from polaris.cells.roles.runtime.internal.engineering_semantic_advisor_pool import RemoteAdvisorPool

            workspace_configs = advisor_configs_from_workspace(command.workspace)
            if workspace_configs:
                semantic_runtime.pool = RemoteAdvisorPool(workspace_configs)
        except Exception as error:  # noqa: BLE001 - optional assist wiring must never terminate the run
            store.append_event(
                command.session_id,
                kind="run_diagnostic",
                role=command.role,
                payload={
                    "area": "semantic_advisor_pool_config",
                    "error": f"{type(error).__name__}: {error}",
                    "fatal": False,
                },
            )
    from polaris.cells.roles.runtime.internal.engineering_cartographer import deterministic_run_card
    from polaris.cells.roles.runtime.internal.engineering_epoch_kimi import kimi_epoch_pool_from_environment
    from polaris.cells.roles.runtime.internal.engineering_epoch_runtime import EpochRuntime, facts_for_epoch

    # 默认 auto：配了 devourer provider 就开，没配就静默不开。上一版默认 "0"，
    # 结果吞噬兽再也没被创建过（实测 run 里 cartographer/semantic 都有事件，
    # epoch_* 一条没有）。显式关掉用 KERNELONE_CONTEXTOS_EPOCH=0。
    epoch_setting = os.environ.get("KERNELONE_CONTEXTOS_EPOCH", "auto").strip().lower()
    director_frame = command.role == "director"
    epoch_pool = (
        kimi_epoch_pool_from_environment() if director_frame and epoch_setting not in {"0", "false", "off"} else None
    )
    epoch_on = epoch_setting in {"1", "true", "on", "auto"} and epoch_pool is not None
    fallback_pool = (
        semantic_runtime.pool
        if epoch_on
        and semantic_runtime is not None
        and os.environ.get("KERNELONE_CONTEXTOS_EPOCH_FALLBACK", "0").strip().lower() not in {"0", "false", "off"}
        else None
    )
    epoch_runtime = (
        EpochRuntime(epoch_pool or fallback_pool) if epoch_pool is not None or fallback_pool is not None else None
    )
    cartographer_runtime = None
    if director_frame and os.environ.get("KERNELONE_CONTEXTOS_CARTOGRAPHER", "0").strip().lower() not in {
        "0",
        "false",
        "off",
    }:
        from polaris.cells.roles.runtime.internal.engineering_cartographer import (
            CartographerRuntime,
            cartographer_pool_from_environment,
        )

        cartographer_pool = cartographer_pool_from_environment()
        if cartographer_pool is not None:
            cartographer_runtime = CartographerRuntime(cartographer_pool)
    semantic_reported: set[str] = set()
    epoch_reported: set[str] = set()
    cartographer_reported: set[str] = set()
    last_injection_fingerprint = ""
    last_run_card_key: tuple[str, str] | None = None

    capability_last_used: dict[str, int] = {}
    capability_last_reminded: dict[str, int] = {}
    resident_stage: str | None = None
    resident_profile_fingerprint = ""
    tools = EngineeringTools(
        command.workspace,
        scope,
        limits,
        cancel,
        consult=consult_current if consult is not None and command.role == "director" else None,
        review=review_current if command.role == "qa" else None,
        verify=verify_current if verify is not None and command.role == "director" else None,
        observe=observe_current if observe is not None and command.role in {"director", "qa"} else None,
        authorize=authorize,
        artifact_binding=artifact_binding,
        review_clauses=review_clauses,
        run_id=command.session_id,
        file_card_resolver=resolve_file_card,
        revision_provider=lambda: int(
            store.load_run(command.session_id, workspace=command.workspace)["workspace_revision"]
        ),
    )
    if project_run:
        saved = store.load_run(command.session_id, workspace=command.workspace)
        if saved["status"] not in {"pending", "running"}:
            # A cold invocation is an observation until the service explicitly
            # admits a resume. It must not append a new frame or dispatch.
            return RoleRunResult(
                saved["session_id"],
                saved["role"],
                saved["text"],
                saved["status"],
                saved["verification"],
                tuple(ToolObservation(**item) for item in saved["checks"]),
            )

    events = store.load_events(command.session_id)
    starts = [event for event in events if event["kind"] == "role_started" and event["role"] == command.role]
    last_start = starts[-1] if starts else None
    unfinished = last_start is not None and not any(
        event["kind"] == "role_finished" and event["role"] == command.role and event["seq"] > last_start["seq"]
        for event in events
    )
    if (
        unfinished
        and last_start is not None
        and last_start["payload"].get("instruction") == command.instruction
        and last_start["payload"].get("purpose") == purpose
    ):
        frame_seq = last_start["seq"]
        messages = store.load_message_history(command.session_id, role=command.role, after_seq=frame_seq)
        if not director_frame:
            # Older frames persisted the Director's previous-run guidance as a system message.
            messages = [
                message
                for message in messages
                if not (
                    message.get("role") == "system"
                    and str(message.get("content", "")).startswith("ContextOS 跨运行记忆（摘要，非验收证据）：")
                )
            ]
    else:
        frame_seq = store.append_event(
            command.session_id,
            kind="role_started",
            role=command.role,
            payload={"instruction": command.instruction, "purpose": purpose},
        )
        messages = []
    # A continued frame replays the earlier turns of the same run. Their
    # observations describe the state at the time they ran, and a model that
    # reads one as the current state restarts work that is already done.
    # The continuation note only warns about stale tool observations from
    # prior turns; it is not needed when there are only initial messages
    # (e.g. a fresh run with system + user instruction but no tool results yet).
    history_notice_sent = False
    # Resident AGI state must exist for every frame. A continued frame replays
    # earlier turns and skips the fresh-frame setup below; leaving these unbound
    # made the first resident check raise UnboundLocalError and kill the resume.
    resident_decision: dict[str, Any] | None = None
    resident_runtime: Any = None
    if not messages:
        system_message = {
            "role": "system",
            "content": role_instruction(
                command.role,
                visual="browser_observe" in tools.enabled,
                observation_mode=scope.browser.observation_mode if scope.browser else "images",
                require_visual_review=bool(scope.browser and scope.browser.require_visual_review),
                require_qa=bool(store.load_run(command.session_id, workspace=command.workspace)["require_qa"]),
            ),
        }
        prior_messages: list[dict[str, Any]] = []
        if director_frame:
            try:
                from polaris.cells.roles.runtime.internal.engineering_memory_cards import build_prior_run_memory

                previous = [
                    row
                    for row in store.list_runs(workspace=command.workspace, limit=3)
                    if row.get("session_id") != command.session_id
                ]
                if previous:
                    prior_messages = build_prior_run_memory(
                        previous[0], store.load_events(str(previous[0]["session_id"]))
                    )
            except (KeyError, OSError, ValueError):
                prior_messages = []
        profile_messages: list[dict[str, Any]] = []
        if os.environ.get("KERNELONE_RESIDENT_AGI", "0").strip().lower() in {
            "1",
            "true",
            "on",
        }:
            from polaris.cells.roles.runtime.internal.engineering_resident_agi import (
                ResidentAGIRuntime,
                collect_signals,
                facts_for_decision,
                resident_agi_pool_from_environment,
            )

            resident_pool = resident_agi_pool_from_environment()
            if resident_pool is not None:
                resident_runtime = ResidentAGIRuntime(resident_pool)
                resident_runtime.start(
                    facts_for_decision(
                        command.workspace,
                        [],
                        {
                            "verification": "not_run",
                            "workspace_revision": 0,
                            "status": "running",
                        },
                    )
                )
        if resident_runtime is not None:
            try:
                from polaris.cells.roles.runtime.internal.engineering_resident_agi import _normalize_decision

                signals = collect_signals(command.workspace)
                raw = resident_runtime.poll()
                deadline = time.monotonic() + 75.0
                while raw is None and time.monotonic() < deadline:
                    await asyncio.sleep(0.5)
                    raw = resident_runtime.poll()
                if raw:
                    resident_decision = _normalize_decision(raw, signals)
            except Exception as error:  # noqa: BLE001 - agent wiring must never abort a run
                store.append_event(
                    command.session_id,
                    kind="agent_channel_error",
                    role="resident_agi",
                    payload={"stage": "first_decision", "error_type": type(error).__name__, "error": str(error)[:300]},
                )
                resident_decision = None
        if resident_decision is None and os.environ.get(
            "KERNELONE_RESIDENT_PROJECT_PROFILE", "1"
        ).strip().lower() not in {
            "0",
            "false",
            "off",
        }:
            from polaris.cells.roles.runtime.internal.engineering_project_profile import (
                detect_project_profile,
                prompt_additions,
            )

            profile = detect_project_profile(command.workspace)
            resident_decision = {
                "schema_version": "resident_agi_decision.v1",
                "project_type": profile["project_type"],
                "capabilities": profile["capabilities"],
                "mcp_hints": profile["mcp_hints"],
                "guidance": profile["guidance"],
                "reason": "规则回退（Resident AGI 不可用或未返回）",
                "signals": profile["signals"],
            }
            profile_messages = prompt_additions(profile)
        if resident_decision is not None:
            if not profile_messages:
                from polaris.cells.roles.runtime.internal.engineering_project_profile import prompt_additions

                profile_messages = prompt_additions(resident_decision)
            store.append_event(
                command.session_id,
                kind="resident_agi_decision",
                role="resident_agi",
                payload=resident_decision,
            )
        messages = [
            system_message,
            *profile_messages,
            *prior_messages,
            {"role": "user", "content": command.instruction},
        ]
        for message in messages:
            store.append_message(command.session_id, role=command.role, message=message)
    if project_run:
        store.update_run(command.session_id, status="running")
    output_bytes = 0

    def pending_calls() -> list[dict[str, Any]]:
        return [
            call
            for call in store.pending_tool_calls(command.session_id)
            if (call["turn_seq"], call["call_id"]) not in pending_ancestors
        ]

    def finish(status: Literal["completed", "failed", "cancelled", "interrupted"], text: str) -> RoleRunResult:
        pending = pending_calls()
        if pending:
            # Dispatch is durable before an effect; only a durable observation
            # can close it. Exceptions/cancellation cannot prove no effect.
            status = "interrupted"
            text = (
                f"{len(pending)} dispatched tool call(s) have no durable observation; effects are unknown. "
                f"Reconcile actual effects before continuing; automatic replay is blocked. {text}"
            )
        result = RoleRunResult(
            command.session_id,
            command.role,
            bounded_output(text, limits.max_output_bytes),
            status,
            "unknown" if status == "interrupted" else "not_run",
            (),
        )
        store.append_event(
            command.session_id,
            kind="role_finished",
            role=command.role,
            payload={"frame_seq": frame_seq, **asdict(result)},
        )
        if project_run:
            store.update_run(
                command.session_id, status=result.status, text=result.text, verification=result.verification, checks=()
            )
        return result

    context_window_tokens = _model_context_window(Path(command.workspace), command.role)
    # Host-side loop detection: a weak model can repeat the same probe forever
    # while admitting it is looping. Track consecutive identical turn
    # signatures and inject evidence-based recovery guidance at thresholds.
    loop_signature: str | None = None
    loop_run_count = 0
    loop_alert: str | None = None
    last_turn_evidence: list[str] = []
    # Gate-block streak: turns whose every call was intercepted by the stage
    # gate. Weak models keep re-issuing blocked probes; after a short streak
    # inject an explicit pivot alert (read_file stays allowed, but only
    # verify_project / browser_observe unblocks the gate).
    gate_block_streak = 0
    # Stage gate (lab-then-assemble, enforced by the host rather than left to
    # the model): after this many file writes the Director must run
    # verify_project before it may write again, turning free-form flailing
    # into a build -> verify -> fix rhythm. 0 disables the gate.
    # Off by default: counting raw writes is the wrong unit. A weak model that
    # scaffolds several files hits the limit mid-scaffold and sees a stream of
    # write_file/edit_file rejections, while "no unverified delivery" is already
    # enforced by the completion gate (_verify_director) plus wrap-up and QA.
    # Opt in (e.g. 5) only where a write-then-verify rhythm is explicitly wanted.
    stage_gate_writes = int(os.environ.get("KERNELONE_ENGINEERING_STAGE_GATE_WRITES", "0") or "0")
    writes_since_verification = 0
    wrap_up_gate_enabled = os.environ.get(_WRAP_UP_GATE_ENV, "1") != "0"
    wrap_up_force_finish = False
    # Persistent across turns (unlike the per-turn guidance flag): the current
    # candidate has a passing verification that any explicit source write voids.
    candidate_verification_passed = False
    # Pass -> write cycles already spent. One legitimate change-then-reverify
    # cycle is allowed; a second means the model is thrashing its own evidence,
    # so further explicit writes are intercepted. The count is run-scoped: a
    # frame restart must not give an over-working model a fresh budget.
    wrap_up_cycle_allowance = _wrap_up_cycle_allowance()
    prior_wrap_blocks = sum(1 for _event in store.load_events(command.session_id, kinds=("wrap_up_enforced",)))
    post_pass_write_cycles = 1 if prior_wrap_blocks >= 1 else 0
    wrap_up_blocks = prior_wrap_blocks
    # A check that tried to write during read-only verification (EROFS) will fail
    # deterministically again. Require an actual writing execute_command
    # (preparation) before another verify_project may run, instead of letting a
    # weak model re-issue the same doomed check forever.
    preparation_pending = False
    # The exact check commands that armed the gate. Only an identical re-run is
    # blocked: a genuinely new read-only check must still be able to execute,
    # otherwise the gate would hide the preparation step it asks for.
    preparation_pending_commands: tuple[str, ...] = ()
    first_verification_fraction = float(
        os.environ.get(_FIRST_VERIFICATION_FRACTION_ENV, str(_FIRST_VERIFICATION_FRACTION_DEFAULT)) or 0
    )
    qa_turn_limit = int(os.environ.get(_QA_TURN_LIMIT_ENV, str(_QA_TURN_LIMIT_DEFAULT)) or 0)
    qa_turns = 0
    qa_min_fraction = min(
        0.8,
        max(0.0, float(os.environ.get(_QA_MIN_FRACTION_ENV, str(_QA_MIN_FRACTION_DEFAULT)) or 0)),
    )
    # 到点必须把候选交给独立审查，否则审查会被压到没有时间读源码。
    if limits.deadline is not None and qa_min_fraction > 0:
        _created_at = float(store.load_run(command.session_id, workspace=command.workspace)["created_at"])
        director_deadline = float(limits.deadline) - (float(limits.deadline) - _created_at) * qa_min_fraction
    else:
        director_deadline = None
    # The host selected the run's shell policy (bash_pipefail_v1). Ordinary
    # execute_command must honour it too: under the POSIX default a piped command
    # such as `npm run build 2>&1 | tail` masks the real exit status, so a failed
    # build looks successful and the model stops repairing it.
    execution_policy = resolve_execution_policy(
        store.load_run(command.session_id, workspace=command.workspace).get("verification_execution_policy")
    )
    try:
        if pending_calls():
            return finish("interrupted", "An earlier dispatch still requires reconciliation.")
        while True:
            if cancel.is_set():
                return finish("cancelled", "Engineering role cancelled; no further tools dispatched.")
            if limits.deadline is not None and time.time() >= limits.deadline:
                return finish("failed", "Engineering run deadline exceeded.")
            turn_seq = store.append_event(command.session_id, kind="turn_started", role=command.role, payload={})
            if command.role == "qa" and qa_turn_limit > 0:
                qa_turns += 1
                if qa_turns > qa_turn_limit + _QA_TURN_GRACE:
                    return finish(
                        "interrupted",
                        "QA review exceeded its effort budget without submitting report_review; "
                        "the candidate keeps its current verdict (unknown).",
                    )
            if semantic_runtime is not None:
                advice = semantic_runtime.poll()
                advice_fingerprint = str(advice.get("fingerprint") or "") if advice is not None else ""
                if advice is not None and advice_fingerprint not in semantic_reported:
                    store.append_event(
                        command.session_id,
                        kind="semantic_advice_completed",
                        role="distiller",
                        payload={**advice, "agent_role": "distiller"},
                    )
                    semantic_reported.add(advice_fingerprint)
            if epoch_runtime is not None:
                epoch_card = epoch_runtime.poll()
                if epoch_card is not None and epoch_card["fingerprint"] not in epoch_reported:
                    store.append_event(
                        command.session_id,
                        kind="epoch_card_completed",
                        role="devourer",
                        payload={**epoch_card, "agent_role": "devourer"},
                    )
                    epoch_reported.add(epoch_card["fingerprint"])
            run_now, journal_events = store.load_projection_inputs(
                command.session_id, workspace=command.workspace, kinds=None
            )
            context_input_seq = journal_events[-1]["seq"]
            # 只有在候选已经通过验证之后才让位：那之前强行收尾只会把未验证的
            # 候选交给审查，而首次验证本身有独立预算点。
            if (
                command.role == "director"
                and director_deadline is not None
                and candidate_verification_passed
                and time.time() >= director_deadline
            ):
                # 到点让位：独立审查需要足读源码与截图的时间，否则它会交出一份
                # "证据不足"的裁决，整轮停在 unknown（实测 L1-01：审查刚要读源码
                # 就被 reserve 强制收尾）。
                store.append_event(
                    command.session_id,
                    kind="qa_share_enforced",
                    role="director",
                    payload={
                        "turn_seq": turn_seq,
                        "qa_min_fraction": qa_min_fraction,
                        "verification": run_now.get("verification"),
                    },
                )
                return finish(
                    "completed",
                    f"Director reached the host's QA share boundary ({qa_min_fraction:.0%} of the run "
                    "budget reserved for independent review). Handing the current candidate to QA.",
                )
            if epoch_runtime is not None:
                epoch_request, epoch_refs, epoch_fingerprint = facts_for_epoch(run_now, journal_events)
                if epoch_refs.node_ids:
                    epoch_runtime.schedule(epoch_request, epoch_refs, fingerprint=epoch_fingerprint)
            first_verification_due = False
            if (
                command.role == "director"
                and "verify_project" in tools.enabled
                and first_verification_fraction > 0
                and limits.deadline is not None
                and run_now.get("verification") == "not_run"
            ):
                created_at = float(run_now["created_at"])
                budget = max(0.0, float(limits.deadline) - created_at)
                first_verification_due = time.time() >= created_at + budget * first_verification_fraction
            from polaris.cells.roles.runtime.internal.engineering_phase_policy import phase_for

            director_phase = (
                phase_for(
                    command.role,
                    verification=str(run_now.get("verification") or "not_run"),
                    first_verification_due=first_verification_due,
                    preparation_pending=preparation_pending,
                    qa_report_required=False,
                )
                if director_frame
                else None
            )
            card_run = {**run_now, "execution_phase": director_phase.value if director_phase else None}
            cartographer_card: dict[str, Any] | None = None
            if director_frame and cartographer_runtime is None and turn_seq == 1:
                store.append_event(
                    command.session_id,
                    kind="cartographer_disabled",
                    role="cartographer",
                    payload={"reason": "pool_unavailable"},
                )
            if cartographer_runtime is not None:
                from polaris.cells.roles.runtime.internal.engineering_cartographer import (
                    NarrativeRejectedError,
                    current_facts,
                    facts_for_run_card,
                    parse_cartographer_response,
                )

                narrative = cartographer_runtime.poll()
                if narrative is not None and await asyncio.to_thread(
                    cartographer_runtime.retire_delivered_probe,
                    journal_events,
                    workspace=str(command.workspace),
                    session_id=command.session_id,
                ):
                    narrative = None
                if narrative is not None:
                    parsed = parse_cartographer_response(narrative)
                    cartographer_events = journal_events
                    facts = current_facts(card_run, cartographer_events)
                    observed_phase = facts.get("phase")
                    returned_phase = (parsed or {}).get("phase") if isinstance(parsed, dict) else None
                    try:
                        validated = cartographer_runtime.validate_current(parsed, facts)
                    except NarrativeRejectedError as exc:
                        validated = None
                        detail = exc.detail or exc.code
                        if cartographer_runtime.note_rejection(exc.code, detail):
                            store.append_event(
                                command.session_id,
                                kind="cartographer_rejected",
                                role="cartographer",
                                payload={
                                    "reason": exc.code,
                                    "raw_response": narrative,
                                    "prior_raw_responses": cartographer_runtime.prior_raw_responses,
                                    "detail": detail,
                                    # 远程那一跳的真实原因（超时/空响应/HTTP），
                                    # 否则拒绝详情永远只是一句 NoneType。
                                    "transport_error": cartographer_runtime.last_error,
                                    "observed_phase": observed_phase,
                                    "returned_phase": returned_phase,
                                    "fingerprint": cartographer_runtime.fingerprint,
                                },
                            )
                    else:
                        if validated is None:
                            detail = f"returned_phase={returned_phase!r} observed_phase={observed_phase!r}"
                            if cartographer_runtime.note_rejection("narrative_disagrees_with_facts", detail):
                                store.append_event(
                                    command.session_id,
                                    kind="cartographer_rejected",
                                    role="cartographer",
                                    payload={
                                        "reason": "narrative_disagrees_with_facts",
                                        "raw_response": narrative,
                                        "prior_raw_responses": cartographer_runtime.prior_raw_responses,
                                        "detail": detail,
                                        "observed_phase": observed_phase,
                                        "returned_phase": returned_phase,
                                        "fingerprint": cartographer_runtime.fingerprint,
                                    },
                                )
                    if validated is not None and validated.get("outcome") == "abstained":
                        store.append_event(
                            command.session_id,
                            kind="cartographer_abstained",
                            role="cartographer",
                            payload={
                                **validated,
                                "fingerprint": cartographer_runtime.fingerprint,
                                "validation_result": "VALID",
                                "raw_response": narrative,
                                "prior_raw_responses": cartographer_runtime.prior_raw_responses,
                            },
                        )
                    elif validated is not None:
                        cartographer_card = {
                            **validated,
                            **({"cognitive_focus": validated["phase"]} if "phase" in validated else {}),
                            "raw_response": narrative,
                            "prior_raw_responses": cartographer_runtime.prior_raw_responses,
                            "execution_phase": facts["execution_phase"],
                            "agent_role": "cartographer",
                        }
                        key = f"{cartographer_runtime.fingerprint}:{json.dumps(validated, sort_keys=True, ensure_ascii=False)}"
                        if key not in cartographer_reported:
                            store.append_event(
                                command.session_id,
                                kind="cartographer_completed",
                                role="cartographer",
                                payload=cartographer_card,
                            )
                            cartographer_reported.add(key)
            visual_projection: dict[str, Any] = {}
            from polaris.cells.roles.runtime.internal.engineering_context_budget import context_fold_window
            from polaris.cells.roles.runtime.internal.engineering_tool_payload_projection import (
                message_text_tokens,
            )

            # 保留判据只看文本：截图是几千个 vision token，不是它的 base64 拼写
            # 那样几十万。按字节计价会让任何有浏览器证据的 run 越过 256k 预留，
            # 把折叠窗口从 24 腰斩到 12 —— 这正是"折叠→重读→再折叠"循环的成因。
            # 整轮丢弃另有 aligned 成本（含图像）来把关。
            history_tokens = sum(message_text_tokens(message) for message in messages)
            fold_window: int | None = context_fold_window(
                limits=limits,
                created_at=float(run_now["created_at"]),
                current_tokens=history_tokens,
                context_window_tokens=context_window_tokens,
            )
            fold_window = _retention_fold_window(
                fold_window,
                history_tokens=history_tokens,
                context_window_tokens=context_window_tokens,
            )
            all_turns = [
                event["seq"]
                for event in journal_events
                if event.get("kind") == "turn_started" and event["seq"] < turn_seq
            ]
            stall_streak = _stall_streak(journal_events, all_turns[-3:]) if all_turns else 0
            repeated_reads = _repeated_read_paths(journal_events)
            stalled = director_frame and stall_streak >= 3
            director_guidance: list[str] = []
            pending_guidance_events: list[tuple[str, str, dict[str, Any]]] = []
            idle_capabilities: list[str] = []
            next_injection_fingerprint = last_injection_fingerprint
            if stalled:
                director_guidance.append(_stall_message(stall_streak, repeated_reads)["content"])
                pending_guidance_events.append(
                    (
                        "stall_intervention",
                        command.role,
                        {"streak": stall_streak, "repeated_reads": repeated_reads, "turn_seq": turn_seq},
                    )
                )
            if resident_runtime is not None:
                from polaris.cells.roles.runtime.internal.engineering_resident_agi import (
                    _normalize_decision,
                    collect_signals,
                    detect_stage,
                    facts_for_decision,
                )

                stage = detect_stage(journal_events, run_now)
                signals = collect_signals(command.workspace)
                # The first decision runs before the workspace has any project
                # files, so its signals are empty. Re-ask with fresh signals once
                # the stage, revision or repository signature actually changes.
                fingerprint = hashlib.sha256(
                    json.dumps(
                        {
                            "stage": stage,
                            "revision": run_now.get("workspace_revision"),
                            "signals": signals["signals"],
                        },
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest()
                # Consume a finished decision before scheduling a new one;
                # otherwise start() would replace an already-paid-for result.
                raw = resident_runtime.poll()
                if raw:
                    refreshed = _normalize_decision(raw, signals)
                    if refreshed:
                        resident_decision = refreshed
                        resident_stage = stage
                        # Only a real decision consumes the fingerprint, so a
                        # failed ask is retried on the next eligible turn.
                        resident_profile_fingerprint = fingerprint
                        store.append_event(
                            command.session_id,
                            kind="resident_agi_decision",
                            role="resident_agi",
                            payload={**refreshed, "reassessed": True},
                        )
                if resident_runtime.idle and fingerprint != resident_profile_fingerprint and turn_seq % 8 == 0:
                    resident_runtime.start(facts_for_decision(command.workspace, journal_events, run_now))
                resident_stage = stage or resident_stage
                bound_capabilities = (resident_decision or {}).get("capabilities") or []
                for event in journal_events[-40:]:
                    if event.get("kind") != "tool_observed":
                        continue
                    tool = event["payload"].get("tool")
                    if tool in bound_capabilities:
                        capability_last_used[tool] = turn_seq
                idle_capabilities = [
                    capability
                    for capability in (resident_decision or {}).get("capabilities") or []
                    if capability_last_used.get(capability, -(10**9)) <= turn_seq - 5
                    and capability_last_reminded.get(capability, -(10**9)) <= turn_seq - 10
                ]
                if idle_capabilities:
                    director_guidance.append(
                        "已装配的可用工具：" + "、".join(str(item) for item in idle_capabilities[:4])
                    )
                    pending_guidance_events.append(
                        (
                            "capability_usage_reminder",
                            "resident_agi",
                            {"idle_capabilities": idle_capabilities, "turn_seq": turn_seq},
                        )
                    )
            structured_repairs = (
                _active_structured_repairs(journal_events)
                if director_frame
                and os.environ.get("KERNELONE_ENGINEERING_STRUCTURED_REPAIR", "1").strip().lower()
                not in {"0", "false", "off"}
                else []
            )
            if structured_repairs:
                director_guidance.append(_structured_repair_message(structured_repairs)["content"])
                pending_guidance_events.append(
                    ("structured_repair_engaged", command.role, {"repairs": structured_repairs})
                )
            if director_frame:
                run_card = deterministic_run_card(card_run, journal_events, phase="")
                card_narrative = None
                if cartographer_card is not None:
                    card_narrative = {
                        key: cartographer_card.get(key)
                        for key in ("intent", "reason", "next_actions", "uncertain", "cognitive_focus")
                    }
                    run_card = {
                        **run_card,
                        "narrative": card_narrative,
                        "narrative_evidence_refs": cartographer_card.get("evidence_refs") or [],
                    }
                from polaris.cells.roles.runtime.internal.engineering_memory_cards import (
                    build_context_memory_messages,
                    cartographer_action_proposals,
                    run_card_section,
                )

                proposals = cartographer_action_proposals(run_card)
                memory = build_context_memory_messages(run_now, journal_events)
                sections = [message["content"] for message in memory]
                if memory or proposals:
                    sections.append(run_card_section(run_card))
                card_key = (
                    str(run_card["phase"]),
                    json.dumps(card_narrative, sort_keys=True, ensure_ascii=False),
                )
                if card_key != last_run_card_key:
                    last_run_card_key = card_key
                    store.append_event(
                        command.session_id,
                        kind="run_card_active",
                        role=command.role,
                        payload={
                            "phase": run_card["phase"],
                            "source": "cartographer" if cartographer_card is not None else "host",
                            "turn_seq": turn_seq,
                            "card": run_card,
                        },
                    )
                if sections:
                    content = "\n".join(sections)
                    director_guidance.append(content)
                    fingerprint = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    if fingerprint != last_injection_fingerprint:
                        next_injection_fingerprint = fingerprint
                        kinds = [
                            label
                            for label, marker in (
                                ("failure_cluster", "未解决失败聚类"),
                                ("verification_contract", "最近验证契约"),
                                ("decision_journal", "先前选择（模型自述）"),
                                ("epoch_card", "Epoch 记忆"),
                            )
                            if marker in content
                        ]
                        titles = [str(line)[2:][:120] for line in content.splitlines() if line.startswith("- ")]
                        if memory:
                            pending_guidance_events.append(
                                (
                                    "memory_card_injected",
                                    command.role,
                                    {
                                        "turn_seq": turn_seq,
                                        "kinds": kinds,
                                        "titles": titles[:8],
                                        "chars": len(content),
                                        "digest": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
                                    },
                                )
                            )
                        host_actions = [str(item["action"]) for item in run_card["next_actions"][:3]]
                        if proposals or host_actions:
                            pending_guidance_events.append(
                                (
                                    "run_card_injected",
                                    command.role,
                                    {
                                        "turn_seq": turn_seq,
                                        "phase": run_card.get("phase"),
                                        "source": "cartographer" if proposals else "host",
                                        "next_actions": [item["action"] for item in proposals] or host_actions,
                                        "host_next_actions": host_actions,
                                        "advisory_next_actions": proposals,
                                        **(
                                            {"cartographer_fingerprint": cartographer_runtime.fingerprint}
                                            if proposals and cartographer_runtime is not None
                                            else {}
                                        ),
                                    },
                                )
                            )
            supplemental_text = "\n".join(director_guidance)
            request_messages = _bounded_request_messages(
                messages,
                store=store,
                command=command,
                role=command.role,
                context_window_tokens=context_window_tokens,
                visual_binding=visual_binding if scope.browser is not None else None,
                audit=visual_projection,
                semantic_runtime=semantic_runtime,
                fold_window=fold_window,
                force_fold_paths=repeated_reads if stalled else (),
                supplemental_text=supplemental_text,
            )
            if visual_projection.get("supplemental_context_omitted"):
                supplemental_text = ""
            if supplemental_text:
                # Move the already-budgeted tail into the final host user turn.
                tail = request_messages[-1]
                if tail.get("role") != "user" or tail.get("content") != supplemental_text:
                    raise ValueError("Director supplemental context lost during request projection")
                request_messages.pop()
            options: dict[str, Any] = {
                "messages": request_messages,
                "tools": tools.schemas(),
            }
            if first_verification_due:
                # The first verification may not be postponed until the run is
                # about to expire. Keep build preparation available, but stop
                # advertising more source writes/repository exploration.
                from polaris.cells.roles.runtime.internal.engineering_phase_policy import (
                    EngineeringPhase,
                    allowed_tools,
                )

                allowed = allowed_tools(EngineeringPhase.VERIFY) or frozenset()
                options["tools"] = [
                    schema
                    for schema in options["tools"]
                    if ((schema.get("function") or {}).get("name") or schema.get("name")) in allowed
                ]
            if options["tools"]:
                options["tool_choice"] = "auto"
            if command.role == "director" and "verify_project" in tools.enabled:
                # Saved ceiling only: no filesystem probe, current-host promise,
                # permission change, or mutation of durable message history.
                options["messages"].append(
                    {
                        "role": "user",
                        "content": (
                            "Host verification output declarations (original saved state, not current authorization): "
                            + json.dumps(
                                {
                                    "declared_output_roots": (
                                        [str(root) for root in verification_output_declarations]
                                        if verification_output_declarations is not None
                                        else None
                                    ),
                                    "existence_observed": False,
                                    "grants_current_write_permission": False,
                                },
                                ensure_ascii=False,
                            )
                        ),
                    }
                )
            if not history_notice_sent:
                # Only warn about stale observations when a prior turn of this
                # loop actually completed (turn_finished).  Pre-existing tool
                # observations from history setup do not count; the note is
                # only for runs resumed after an earlier turn finished.
                prior_turns = store.load_events(
                    command.session_id,
                    kinds=("turn_finished",),
                    role=command.role,
                )
                if prior_turns:
                    history_notice_sent = True

            # Combine the host continuation note, budget observation and the
            # Director working-state projection into a single user message so
            # protocol-native merging (e.g. Anthropic's _merge_same_role_messages)
            # produces exactly one trailing text block, not separate user turns.
            # Budget observation comes first so protocol-native merging preserves
            # its _MARKER prefix; a single "\n" separator keeps the parts in one
            # text block while still letting _tail_observation find the budget facts.
            shadow_pack = None
            host_parts: list[str] = []
            if command.role == "director" and limits.deadline is not None:
                observed_at = time.time()
                # Keep the original system/history prefix and durable messages
                # unchanged; this observation belongs only to this request.
                host_parts.append(
                    "Host runtime budget observation (request preparation): "
                    f"deadline_epoch_seconds={limits.deadline:.3f}; "
                    f"observed_at_epoch_seconds={observed_at:.3f}; "
                    f"approximate_remaining_seconds={max(0.0, limits.deadline - observed_at):.3f}. "
                    "All work in this run shares the same run deadline. "
                    "This is an observation at preparation time; remaining time may be lower when received."
                )
            if history_notice_sent:
                host_parts.append(_CONTINUATION_HISTORY_NOTE)
            if loop_alert:
                # One-shot recovery guidance; re-arms at the next threshold.
                host_parts.append(loop_alert)
                loop_alert = None
            if first_verification_due:
                host_parts.append(
                    "First-verification budget: no verify_project result exists after "
                    f"{first_verification_fraction:.0%} of the run budget. Run verify_project now with the actual "
                    "build/test commands. execute_command remains available only for build/dependency preparation; "
                    "do not write more source or re-probe the repository before the first verification."
                )
            if command.role == "director":
                # StateFirst context: the bounded working set above drops older
                # turns by design, so the current state must travel with each
                # request. Derived from the same journal only; never persisted.
                from polaris.cells.roles.runtime.internal.engineering_state_projection import (
                    STATE_EVENT_KINDS,
                    shadow_decision_pack,
                    working_state_message,
                )

                run = run_now
                state_events = [event for event in journal_events if event["kind"] in STATE_EVENT_KINDS]
                shadow_pack = shadow_decision_pack(
                    run, state_events, max_tokens=_history_allowances(context_window_tokens)[0]
                )
                host_parts.append(
                    working_state_message(
                        run,
                        state_events,
                        role=command.role,
                        frame_seq=frame_seq,
                        observed_at=time.time(),
                        deadline=limits.deadline,
                    )["content"]
                )
            if command.role in {"director", "qa"} and any(
                (schema.get("function") or schema).get("name") == "read_file" for schema in options["tools"]
            ):
                from polaris.cells.roles.runtime.internal.engineering_memory_cards import (
                    FILE_FACTS_HEADER,
                    build_known_facts_cards,
                )

                fact_cards = build_known_facts_cards(run_now, journal_events, can_read=tools.can_read_file)
                if fact_cards:
                    host_parts.append(
                        FILE_FACTS_HEADER
                        + json.dumps(fact_cards[0]["entries"], ensure_ascii=False, separators=(",", ":"))
                    )
            if supplemental_text:
                host_parts.append(supplemental_text)
            if host_parts:
                options["messages"].append({"role": "user", "content": "\n".join(host_parts)})
            qa_report_required = False
            if command.role == "qa":
                from polaris.cells.roles.runtime.internal.engineering_working_state import (
                    QA_WORKING_STATE_EVENT_KINDS,
                    qa_working_state_message,
                )

                run = run_now
                working_events = [event for event in journal_events if event["kind"] in QA_WORKING_STATE_EVENT_KINDS]
                now = time.time()
                remaining = max(0.0, limits.deadline - now) if limits.deadline is not None else None
                reserve = (
                    qa_review_reserve_seconds(
                        deadline=limits.deadline, created_at=float(run["created_at"]), observed_at=now
                    )
                    if limits.deadline is not None
                    else 0.0
                )
                reason = (
                    "deadline_reserve"
                    if remaining is not None and remaining <= reserve
                    else ("turn_limit" if qa_turn_limit > 0 and qa_turns >= qa_turn_limit else None)
                )
                options["messages"].append(
                    qa_working_state_message(
                        run,
                        working_events,
                        frame_seq=frame_seq,
                        observed_at=now,
                        deadline=limits.deadline,
                        include_evidence_pack=reason is not None,
                    )
                )
                if reason and "report_review" in tools.enabled:
                    source = next(
                        (
                            event["payload"]["call_id"]
                            for event in reversed(working_events)
                            if frame_seq < event["seq"] < turn_seq and _qa_source_read(event)
                        ),
                        None,
                    )
                    if source:
                        qa_report_required = True
                        # Final verdict is a fresh evidence-only call, not a
                        # continuation of dozens of exploratory assistant/tool
                        # turns. The next QA working-state message supplies the
                        # bounded evidence pack and unsatisfied obligations.
                        options["messages"] = [
                            message for message in options["messages"] if message.get("role") == "system"
                        ]
                        # A forced tool_choice is only advisory for some compatible
                        # providers. MiniMax was observed returning execute_command
                        # six times despite type=tool/report_review. Remove every
                        # other tool from the wire as well, then enforce the same
                        # boundary again after parsing before any effect can run.
                        options["tools"] = [
                            schema
                            for schema in options["tools"]
                            if ((schema.get("function") or {}).get("name") or schema.get("name")) == "report_review"
                        ]
                        options["tool_choice"] = {"type": "function", "function": {"name": "report_review"}}
                        # The report request is reduced to system messages on
                        # purpose, so without this digest the reviewer cannot cite
                        # the observations it just made and reports "I hold no
                        # read_file/browser_observe call in this session".
                        evidence_lines: list[str] = []
                        for event in working_events:
                            payload = event["payload"]
                            if event["seq"] <= frame_seq or event["role"] != "qa":
                                continue
                            if payload.get("tool") not in {
                                "read_file",
                                "browser_observe",
                                "execute_command",
                                "repo_rg",
                                "repo_tree",
                            }:
                                continue
                            label = str(payload.get("content") or "")[:160]
                            try:
                                outer = json.loads(str(payload.get("content") or ""))
                                inner = outer.get("content") if isinstance(outer, dict) else None
                                parsed = json.loads(inner) if isinstance(inner, str) else inner
                                if isinstance(parsed, dict):
                                    card = parsed.get("file_card") or parsed.get("command_card") or {}
                                    label = str(card.get("path") or card.get("command") or label)[:160]
                            except json.JSONDecodeError:
                                pass
                            evidence_lines.append(
                                f"{payload.get('call_id')} · {payload.get('tool')} · ok={payload.get('ok')} · "
                                + label.replace("\n", " ")
                            )
                        qa_evidence = "\n".join(evidence_lines[-20:]) or "(no observation in this frame)"
                        # The clause matrix must travel with the ask. Without the
                        # verbatim clauses the reviewer cannot cover them and the
                        # dispatch boundary rejects every report, which burns the
                        # remaining budget in a read/resubmit loop.
                        clause_matrix = "\n".join(
                            f"{index}. {clause}" for index, clause in enumerate(review_clauses, start=1)
                        )
                        # Listing check IDs without their outputs left the reviewer
                        # unable to certify clauses the host had already verified,
                        # so it reported "not provided to me" for every clause.
                        check_lines: list[str] = []
                        for check in run["checks"]:
                            if check.get("ok") is not True:
                                continue
                            raw = str(check.get("content") or "")
                            command_text = raw[:200]
                            try:
                                card = json.loads(raw)
                                command_text = str((card.get("command_card") or {}).get("command") or raw)[:200]
                            except json.JSONDecodeError:
                                pass
                            check_lines.append(
                                f"{check.get('call_id')} · {check.get('tool')} · exit {check.get('exit_code')} · "
                                + command_text.replace("\n", " ")
                            )
                        host_checks = "\n".join(check_lines) or "(no passing host check)"
                        instruction = (
                            f"Current QA source receipt ID: {source}. Your own observations from this frame "
                            "(cite these call IDs as evidence):\n" + qa_evidence + "\n"
                            "Host checks that already passed, with their real command and exit code:\n"
                            + host_checks
                            + "\n"
                            "report_review clause_findings needs one entry for EVERY acceptance clause "
                            f"({len(review_clauses)}). Set clause to that clause's number below (1-based); a range or "
                            "list such as 1-5 or 1,2,3 is accepted. Do not invent your own labels — coverage is "
                            "checked against these numbers:\n" + clause_matrix + "\n"
                            "Cite one of the host check call IDs above as pass evidence when that check covers "
                            "the clause; use your own read_file/browser_observe call IDs for anything you verified "
                            "directly. Put the explanation in feedback. Mark unverifiable clauses unknown, but "
                            "every fail/unknown evidence must state what you actually observed (a file path and "
                            "what is missing, or a call ID) — “Review not started” is not a finding. "
                            "This is the ruling, not a progress report: a matrix whose every clause is unknown "
                            "is rejected and sent back, so rule on the clauses or finish the checks first."
                        )
                    else:
                        instruction = (
                            "Read the relevant source once with an available read tool, then submit report_review."
                        )
                    options["messages"].append(
                        {"role": "user", "content": f"QA effort budget ({reason}). {instruction}"}
                    )
                    store.append_event(
                        command.session_id,
                        kind="qa_report_requested",
                        role="qa",
                        payload={
                            "frame_seq": frame_seq,
                            "turn_seq": turn_seq,
                            "reason": reason,
                            "remaining_seconds": remaining,
                            "reserve_seconds": reserve,
                            "observed_at": now,
                            "deadline": limits.deadline,
                            "source_call_id": source,
                            "tool_choice": options["tool_choice"],
                            "available_tools": sorted(tools.enabled),
                            "acceptance_clauses": list(review_clauses),
                        },
                    )
            phase = director_phase or phase_for(
                command.role,
                verification=str(run_now.get("verification") or "not_run"),
                first_verification_due=first_verification_due,
                preparation_pending=preparation_pending,
                qa_report_required=qa_report_required,
            )
            # Every host injection above can prepend or drop a message after the
            # visual projection recorded its indices. Re-bind the origins against
            # the final request messages so a shifted index cannot make the host
            # read the wrong message and end the run on its own evidence.
            from polaris.kernelone.llm.engine.visual_working_set import (
                rebind_visual_input_sets,
            )

            # Bind against the array that is actually sent: the QA forced-report
            # path replaces options["messages"] with a fresh list, so the earlier
            # request_messages binding is stale there.
            rebound_sets, dropped_sets = rebind_visual_input_sets(
                options["messages"], visual_projection.get("image_sets", [])
            )
            if dropped_sets:
                store.append_event(
                    command.session_id,
                    kind="visual_origin_dropped",
                    role=command.role,
                    payload={"turn_seq": turn_seq, "dropped": dropped_sets},
                )
            visual_projection = {
                **visual_projection,
                "image_sets": rebound_sets,
                "dropped_origins": dropped_sets,
            }
            if supplemental_text:
                included = any(
                    message.get("role") == "user"
                    and isinstance(message.get("content"), str)
                    and supplemental_text in message["content"]
                    for message in options["messages"]
                )
                if included:
                    last_injection_fingerprint = next_injection_fingerprint
                    for capability in idle_capabilities:
                        capability_last_reminded[capability] = turn_seq
                    for kind, event_role, payload in pending_guidance_events:
                        store.append_event(command.session_id, kind=kind, role=event_role, payload=payload)
                else:
                    visual_projection["supplemental_context_omitted"] = {
                        "reason": "reduced_review_request",
                        "chars": len(supplemental_text),
                    }
            if visual_projection.get("supplemental_context_omitted"):
                last_injection_fingerprint = ""
            if semantic_runtime is not None and semantic_runtime.ready:
                from polaris.cells.roles.runtime.internal.engineering_tool_payload_projection import (
                    distillation_targets,
                )

                demand = distillation_targets(options["messages"], fold_window=fold_window)
                advice_request, advice_refs, advice_fingerprint = facts_for_advice(
                    run_now,
                    journal_events,
                    evaluated=semantic_runtime.evaluated_inputs(),
                    targets=demand,
                    role=command.role,
                )
                if advice_refs.node_ids:
                    semantic_runtime.schedule(advice_request, advice_refs, fingerprint=advice_fingerprint)
            if cartographer_runtime is not None:
                run_card_request, run_card_fingerprint = facts_for_run_card(
                    card_run,
                    journal_events,
                    retained_messages=options["messages"],
                    turn_seq=turn_seq,
                    frame_seq=frame_seq,
                )
                if cartographer_runtime.schedule_for_decision(
                    run_card_request,
                    fingerprint=run_card_fingerprint,
                    enabled_tools=frozenset(tools.enabled),
                    recorded_keys={
                        str(e["payload"]["decision_key"])
                        for e in journal_events
                        if e["kind"] == "cartographer_scheduled" and e["payload"].get("decision_key")
                    },
                ):
                    store.append_event(
                        command.session_id,
                        kind="cartographer_scheduled",
                        role="cartographer",
                        payload={
                            "turn_seq": turn_seq,
                            "fingerprint": cartographer_runtime.fingerprint,
                            **cartographer_runtime.decision_receipt,
                        },
                    )
            request = AIRequest(
                task_type=TaskType.DIALOGUE,
                role=command.role,
                input=command.instruction,
                options=options,
                context={
                    "session_id": command.session_id,
                    "workspace": str(command.workspace),
                    "engine_version": "engineering_v2",
                    "turn_seq": turn_seq,
                    "phase": phase.value,
                    "call_id": f"{command.session_id}:{command.role}:{turn_seq}",
                    "visual_input_sets": visual_projection.get("image_sets", []),
                },
            )
            turn_client, routing_audit = (
                route_request(request, context_window_tokens) if route_request else (client, {})
            )
            store.append_event(
                command.session_id,
                kind="request_prepared",
                role=command.role,
                payload={
                    "turn_seq": turn_seq,
                    "evidence_kind": "auxiliary_prepared_request",
                    "context_input_seq": context_input_seq,
                    **({"binding_selection": routing_audit} if routing_audit else {}),
                    "visual_projection": visual_projection,
                    **({"shadow_decision_pack": shadow_pack} if shadow_pack is not None else {}),
                    "request": request.to_dict(),
                },
            )
            try:
                response = await _invoke(turn_client, request, cancel, limits.deadline)
            except _TRANSIENT_PROVIDER_EXCEPTIONS as exc:
                # A truncated or reset stream is not an engineering outcome and
                # no tool from this turn ran. Keep the committed observations
                # and let the same role retry its own turn.
                if _has_remaining_budget(limits):
                    message = _provider_transport_message(exc)
                    store.append_event(
                        command.session_id,
                        kind="message",
                        role=command.role,
                        payload={
                            "message": message,
                            "provider_transport_failure": {
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                            },
                        },
                    )
                    messages.append(message)
                    store.append_event(
                        command.session_id,
                        kind="turn_finished",
                        role=command.role,
                        payload={
                            "turn_seq": turn_seq,
                            "tools_dispatched": False,
                            "provider_transport_failure": True,
                        },
                    )
                    continue
                store.append_event(
                    command.session_id,
                    kind="role_error",
                    role=command.role,
                    payload={
                        "error": f"{type(exc).__name__}: {exc}",
                        "error_type": type(exc).__name__,
                        "deadline_expired": True,
                    },
                )
                return finish("interrupted", f"Provider stream interrupted: {exc}")
            except TimeoutError:
                # The run deadline expired while the provider was answering.
                # The run ends with the facts it committed, not with an
                # exception: the candidate and its checks stay valid, and the
                # verdict the run already reached is kept.
                store.append_event(
                    command.session_id,
                    kind="role_error",
                    role=command.role,
                    payload={
                        "error": "Engineering run deadline exceeded.",
                        "error_type": "TimeoutError",
                        "deadline_expired": True,
                    },
                )
                return finish("interrupted", "Engineering run deadline exceeded.")
            response_seq = store.append_event(
                command.session_id,
                kind="provider_response",
                role=command.role,
                payload={
                    "turn_seq": turn_seq,
                    "ok": response.ok,
                    "raw": response.raw,
                    "error": response.error,
                    "model": response.model,
                    "provider_id": response.provider_id,
                    "trace_id": response.trace_id,
                },
            )
            if cancel.is_set():
                return finish("cancelled", "Engineering role cancelled before tool dispatch.")
            if not response.ok:
                if _is_transport_failure(response) and _has_remaining_budget(limits):
                    # A transport failure is not an engineering outcome. No
                    # tool from this turn ran and the committed observations
                    # stay valid, so the same role continues its own goal with
                    # the recoverable fact instead of ending the run.
                    message = _provider_transport_message(response.error or "unspecified provider error")
                    store.append_event(
                        command.session_id,
                        kind="message",
                        role=command.role,
                        payload={
                            "message": message,
                            "provider_transport_failure": {
                                "error": response.error,
                                "error_category": getattr(response.error_category, "value", None),
                            },
                        },
                    )
                    messages.append(message)
                    store.append_event(
                        command.session_id,
                        kind="turn_finished",
                        role=command.role,
                        payload={
                            "turn_seq": turn_seq,
                            "tools_dispatched": False,
                            "provider_transport_failure": True,
                        },
                    )
                    continue
                return finish("failed", f"Native provider failed: {response.error or 'unspecified provider error'}")
            output_bytes += len(response.output.encode("utf-8"))
            if output_bytes > limits.max_output_bytes:
                return finish("failed", "Engineering role output budget exceeded before tool dispatch.")
            try:
                calls = _native_calls(response)
                if qa_report_required and (not calls or any(call.name != "report_review" for call in calls)):
                    raise NativeTurnRejectedError(
                        "QA effort budget requires only report_review; provider returned an unallowed tool surface",
                        calls,
                        "qa_report_required",
                    )
            except NativeTurnRejectedError as rejection:
                diagnostic = {"turn_seq": turn_seq, "provider_response_seq": response_seq, **rejection.diagnostic}
                message = {
                    "role": "user",
                    "content": (
                        "Your previous native output was rejected before dispatch. No tool from that turn ran; "
                        "earlier observed tool effects are unchanged. Correct the output and continue the original task "
                        "using the same available tools and permissions. For truncated output, use shorter complete "
                        "calls; inspect current files if needed. Rejected candidates below are diagnostic data, "
                        "not executed actions. Full proposals remain in the provider response.\n"
                        + json.dumps(diagnostic, ensure_ascii=False)
                    ),
                }
                # One existing SQL message event commits both rejection facts
                # and the exact recoverable feedback, without orphan tool calls.
                store.append_event(
                    command.session_id,
                    kind="message",
                    role=command.role,
                    payload={"message": message, "native_turn_rejection": diagnostic},
                )
                messages.append(message)
                store.append_event(
                    command.session_id,
                    kind="turn_finished",
                    role=command.role,
                    payload={"turn_seq": turn_seq, "tools_dispatched": False, "output_rejected": True},
                )
                continue
            assistant: dict[str, Any] = {"role": "assistant", "content": response.output}
            if calls:
                assistant["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
                    }
                    for call in calls
                ]
            # Update consecutive-identical-turn loop detection before dispatch.
            # An identical signature means the model re-issued the same probe
            # with the same arguments; a changed signature is real progress.
            turn_signature = (
                json.dumps(
                    sorted(
                        (call.name, json.dumps(call.arguments, sort_keys=True, ensure_ascii=False)) for call in calls
                    ),
                    ensure_ascii=False,
                )
                if calls
                else ""
            )
            if calls and turn_signature == loop_signature:
                loop_run_count += 1
            else:
                loop_run_count = 0
            loop_signature = turn_signature
            if calls and loop_run_count in (3, 7, 15):
                tool_names = ", ".join(sorted({call.name for call in calls}))
                loop_alert = (
                    f"Host loop detection: identical call set ({tool_names}) for {loop_run_count + 1} consecutive turns. "
                    "Previous observations in this frame: "
                    + "; ".join(last_turn_evidence)[:1200]
                    + "\n"
                    + _loop_recovery_instruction(command.role, tools.enabled)
                )
                store.append_event(
                    command.session_id,
                    kind="loop_detected",
                    role=command.role,
                    payload={
                        "turn_seq": turn_seq,
                        "consecutive_identical_turns": loop_run_count + 1,
                        "tools": sorted({call.name for call in calls}),
                        "guidance_injected": True,
                    },
                )
            messages.append(assistant)
            store.append_message(command.session_id, role=command.role, message=assistant)
            if not calls:
                store.append_event(
                    command.session_id, kind="turn_finished", role=command.role, payload={"turn_seq": turn_seq}
                )
                return finish("completed", response.output)
            review_reports: list[dict[str, Any]] = []
            verification_passed = False
            turn_gate_blocks = 0
            last_turn_evidence = []
            for call in calls:
                if cancel.is_set():
                    return finish(
                        "cancelled", "Engineering role cancelled; remaining native calls were not dispatched."
                    )
                try:
                    canonical, normalized = tools.normalize(call.name, call.arguments)
                except ValueError:
                    canonical, normalized = call.name, call.arguments
                # Normalization resolves schema aliases and then drops the alias
                # keys (browser_observe maps path/file/filename onto `entry`). When
                # the tool name is unchanged, keep the model's own keys alongside
                # the canonical ones so the recorded dispatch still answers to what
                # was actually sent; a renamed call (cat -> read_file) keeps the
                # canonical-only record so dispatch and observation still pair.
                recorded_arguments = normalized
                if canonical == call.name and isinstance(call.arguments, dict):
                    recorded_arguments = {
                        **normalized,
                        **{key: value for key, value in call.arguments.items() if key not in normalized},
                    }
                store.record_tool_dispatch(
                    command.session_id,
                    turn_seq=turn_seq,
                    call_id=call.id,
                    tool=canonical,
                    arguments=recorded_arguments,
                    role=command.role,
                )
                from polaris.cells.roles.runtime.internal.engineering_artifacts import (
                    inputs_digest,
                    model_tool_payload,
                    requested_artifacts,
                )
                from polaris.kernelone.process import CommandOutputCapture, open_command_output_capture

                capture_context: contextlib.AbstractContextManager[CommandOutputCapture | None] = (
                    contextlib.nullcontext(None)
                )
                try:
                    if canonical == "execute_command":
                        selected = requested_artifacts(normalized.get("artifacts", []))
                        if selected and tools.artifact_binding is None:
                            raise ValueError("artifact capture has no host candidate binding")
                        if tools.artifact_binding is not None:
                            if authorize is not None:
                                authorize()
                            tools._remaining()
                            if not cancel.is_set():
                                capture_context = open_command_output_capture(command.workspace)
                except (ValueError, OSError):
                    # The normal execution adapter records the same rejection.
                    # No scratch is allocated before real authorization.
                    pass
                with capture_context as output_capture:
                    if output_capture is not None:
                        store.append_event(
                            command.session_id,
                            kind="tool_output_capture_prepared",
                            role=command.role,
                            payload={
                                "turn_seq": turn_seq,
                                "call_id": call.id,
                                "scratch_path": str(output_capture.root),
                            },
                        )
                    try:
                        active_dispatch = (turn_seq, call.id)
                        try:
                            # Single source of truth: the gate message is built
                            # from these exact sets, so guidance and behavior can
                            # never drift apart again (run e6a88b2f's storm).
                            # Only whole-file source writes and workspace probing
                            # are gated. execute_command stays allowed: it is the
                            # ONLY way to prepare/build the candidate (dependency
                            # install, compile) outside the read-only verification
                            # scope. Blocking it deadlocks build-type projects:
                            # the gate demands verify_project while verify_project
                            # cannot build (EROFS) and the preparation path is
                            # blocked (observed 23 execute_command interceptions
                            # across real L1 runs).
                            gate_blocked_tools = ("write_file", "edit_file", "repo_tree", "repo_rg")
                            if (
                                preparation_pending
                                and command.role == "director"
                                and canonical == "verify_project"
                                and tuple(str(item) for item in (normalized.get("commands") or ()))
                                == preparation_pending_commands
                            ):
                                # Preparation gate: the same writing check fails
                                # deterministically under read-only verification.
                                observation = ToolObservation(
                                    call_id=call.id,
                                    tool=canonical,
                                    ok=False,
                                    content=(
                                        "Preparation gate: the previous verification failed because its check tried "
                                        "to write inside the read-only verification scope (EROFS). Re-running the same "
                                        "check will fail the same way. Run the build or preparation with "
                                        "execute_command first (ordinary authorized tools can write the workspace), "
                                        "then verify the prepared candidate with a read-only command."
                                    ),
                                    exit_code=None,
                                    interrupted=False,
                                )
                                store.append_event(
                                    command.session_id,
                                    kind="preparation_gate_enforced",
                                    role=command.role,
                                    payload={"turn_seq": turn_seq, "call_id": call.id},
                                )
                            elif (
                                candidate_verification_passed
                                and wrap_up_gate_enabled
                                and post_pass_write_cycles >= wrap_up_cycle_allowance
                                and command.role == "director"
                                and (
                                    canonical in {"write_file", "edit_file"}
                                    or (
                                        canonical == "execute_command"
                                        and command_may_write(str(normalized.get("command") or ""))
                                    )
                                )
                            ):
                                # Wrap-up gate: the current candidate already
                                # passed verification. An explicit source write
                                # would invalidate that evidence and start a new
                                # verify cycle; intercept it and require a finish.
                                wrap_up_blocks += 1
                                observation = ToolObservation(
                                    call_id=call.id,
                                    tool=canonical,
                                    ok=False,
                                    content=(
                                        "Wrap-up gate: the current candidate's verification already passed. "
                                        "Changing outputs now invalidates that passing evidence and forces a full "
                                        "re-verification. Finish now with a short report of what was built and what "
                                        "the checks showed. Continue only if a declared check actually failed or a "
                                        "requirement is unfinished; host-required checks remain included."
                                    ),
                                    exit_code=None,
                                    interrupted=False,
                                )
                                store.append_event(
                                    command.session_id,
                                    kind="wrap_up_enforced",
                                    role=command.role,
                                    payload={
                                        "turn_seq": turn_seq,
                                        "call_id": call.id,
                                        "tool": canonical,
                                        "blocks": wrap_up_blocks,
                                    },
                                )
                                if wrap_up_blocks >= _WRAP_UP_GATE_MAX_BLOCKS:
                                    wrap_up_force_finish = True
                            elif (
                                command.role == "director"
                                and "verify_project" in tools.enabled
                                and (
                                    first_verification_due
                                    or (stage_gate_writes > 0 and writes_since_verification >= stage_gate_writes)
                                )
                                and canonical in set(gate_blocked_tools)
                            ):
                                # Stage gate: the Director must verify before
                                # writing more OR re-probing the workspace.
                                # Blocking the loop tools closes the escape
                                # hatch where a weak model "explores" instead
                                # of verifying.
                                observation = ToolObservation(
                                    call_id=call.id,
                                    tool=canonical,
                                    ok=False,
                                    content=(
                                        (
                                            "First-verification budget: no verification has completed by the host's "
                                            f"{first_verification_fraction:.0%} budget point. "
                                            if first_verification_due
                                            else f"Stage gate: {writes_since_verification} file writes without a fresh verification. "
                                        )
                                        + "Your ONLY allowed next actions are verify_project with "
                                        "the actual build and test commands, execute_command to prepare or "
                                        "build the candidate (install/compile), browser_observe to render it, "
                                        "or read_file to inspect one specific file. Do NOT run "
                                        + ", ".join(gate_blocked_tools)
                                        + " until verification has run. "
                                        + _stage_gate_verification_hint(command.workspace)
                                    ),
                                    exit_code=None,
                                    interrupted=False,
                                )
                                store.append_event(
                                    command.session_id,
                                    kind="stage_gate_enforced",
                                    role=command.role,
                                    payload={
                                        "turn_seq": turn_seq,
                                        "call_id": call.id,
                                        "writes_since_verification": writes_since_verification,
                                        "first_verification_due": first_verification_due,
                                    },
                                )
                            elif loop_run_count >= 3:
                                # Loop enforcement: the 4th identical probe is
                                # intercepted, not executed, so the model must
                                # change action instead of looping forever.
                                observation = ToolObservation(
                                    call_id=call.id,
                                    tool=canonical,
                                    ok=False,
                                    content=(
                                        f"Loop break: you repeated the identical {canonical} call for the last "
                                        f"{loop_run_count + 1} turns. "
                                        + _loop_recovery_instruction(command.role, tools.enabled)
                                    ),
                                    exit_code=None,
                                    interrupted=False,
                                )
                                loop_run_count = 0
                                store.append_event(
                                    command.session_id,
                                    kind="loop_enforced",
                                    role=command.role,
                                    payload={"turn_seq": turn_seq, "call_id": call.id, "tool": canonical},
                                )
                            elif output_capture is not None:
                                observation = await tools.execute(
                                    call.id,
                                    canonical,
                                    normalized,
                                    execution_policy=execution_policy,
                                    output_capture=output_capture,
                                )
                            else:
                                observation = await tools.execute(
                                    call.id, canonical, normalized, execution_policy=execution_policy
                                )
                        finally:
                            active_dispatch = None
                        if observation.tool in {"write_file", "edit_file"} and observation.ok:
                            writes_since_verification += 1
                        elif observation.tool == "verify_project":
                            # Any actual verification run (pass or fail) opens
                            # the next stage; its real output guides the fix.
                            writes_since_verification = 0
                        advances_revision = (observation.tool in {"write_file", "edit_file"} and observation.ok) or (
                            observation.tool == "execute_command"
                            # A read-only probe changes nothing, so it must not
                            # invalidate a passing verification nor disarm the
                            # wrap-up gate; only a command that may write does.
                            # Two exceptions must still invalidate: a killed or
                            # timed-out command whose effect is unknown, and a
                            # command run where the sandbox cannot physically
                            # guarantee read-only (network reachable, or a
                            # read-write command cache).
                            #
                            # 候选 owner 判定：只有 Director 能推进候选 revision。
                            # QA/PM/CE 默认只读（write_roots=()、network 关、
                            # command_cache=read_only），它们的探测是验证动作，不是
                            # 候选变更。旧版只看命令文本，于是 QA 的
                            # `ls … 2>&1` / `npm test` 因 command_may_write 误判
                            # （2>&1 里的 >）把 revision 一路顶掉：实测 L1-01
                            # rev111 → 133、verification 被重置 not_run，最后
                            # 14 份 qa_review_completed 全部 verdict=unknown 且
                            # report_call_id=None —— 报告被整体丢弃，run 恒
                            # interrupted。'同 workspace 仅一个源码写入者'。
                            and command.role == "director"
                            and (
                                command_may_write(str(normalized.get("command") or ""))
                                or observation.timed_out
                                or observation.interrupted
                                or tools.scope.network_allowed
                                or tools.scope.command_cache == "read_write"
                            )
                            and (observation.exit_code is not None or observation.interrupted or observation.timed_out)
                        )
                        if advances_revision:
                            # A durable write advances the candidate revision, so
                            # the earlier pass no longer describes the current
                            # outputs and the wrap-up gate must re-arm.
                            if candidate_verification_passed:
                                post_pass_write_cycles += 1
                            verification_passed = False
                            candidate_verification_passed = False
                        if output_capture is not None and observation.artifacts:
                            run = store.load_run(command.session_id, workspace=command.workspace)
                            binding = {}
                            if observation.artifacts and artifact_binding is not None:
                                try:
                                    binding = artifact_binding()
                                except (ValueError, OSError):
                                    # The ordinary command observation remains
                                    # valid even if its optional stable capture
                                    # binding cannot be established.
                                    binding = {}
                            digest = inputs_digest(binding) if binding else None
                            stable = bool(observation.artifacts) and all(
                                item.source_digest == digest for item in observation.artifacts
                            )
                            store.record_tool_observation(
                                command.session_id,
                                turn_seq=turn_seq,
                                observation=observation,
                                role=command.role,
                                advance_workspace_revision=advances_revision,
                                expected_workspace_revision=int(run["workspace_revision"]),
                                **({"capture_completion": {"input_binding": binding}} if stable and binding else {}),
                            )
                        else:
                            store.record_tool_observation(
                                command.session_id, turn_seq=turn_seq, observation=observation, role=command.role
                            )
                            if advances_revision:
                                run = store.load_run(command.session_id, workspace=command.workspace)
                                store.update_run(
                                    command.session_id,
                                    workspace_revision=int(run["workspace_revision"]) + 1,
                                    verification="not_run",
                                    checks=(),
                                )
                    except BaseException:
                        if output_capture is not None:
                            output_capture.retain_for_reconciliation()
                        raise
                last_turn_evidence.append(
                    f"{observation.tool} {observation.call_id} ok={observation.ok}: {observation.content[:300]}"
                )
                if observation.tool == "report_review" and observation.ok and not observation.interrupted:
                    review_reports.append(tool_observation_payload(observation))
                if observation.tool == "verify_project" and observation.ok and not observation.interrupted:
                    # The candidate has a passing current verification. QA has to
                    # review that same candidate, so the turn has to conclude.
                    verification_passed = True
                    candidate_verification_passed = True
                if observation.tool == "verify_project" and not observation.ok:
                    from polaris.cells.roles.runtime.internal.engineering_verification import (
                        is_read_only_failure,
                    )

                    if is_read_only_failure(str(observation.content or "")):
                        preparation_pending = True
                        preparation_pending_commands = tuple(str(item) for item in (normalized.get("commands") or ()))
                if advances_revision and observation.tool == "execute_command":
                    # An actual writing preparation step clears the EROFS gate.
                    preparation_pending = False
                    preparation_pending_commands = ()
                message = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(
                        model_tool_payload(tool_observation_payload(observation)), ensure_ascii=False
                    ),
                }
                messages.append(message)
                store.append_message(command.session_id, role=command.role, message=message)
                if observation.interrupted or pending_calls():
                    # A nested read role can also lose a durable observation.
                    # No later parent effect may run while that dispatch is unknown.
                    return finish("cancelled" if cancel.is_set() else "interrupted", observation.content)
                if observation.content.startswith("Stage gate:"):
                    turn_gate_blocks = turn_gate_blocks + 1
                else:
                    turn_gate_blocks = 0 if observation.ok or observation.exit_code is not None else turn_gate_blocks
            if wrap_up_force_finish:
                return finish(
                    "completed",
                    "Current candidate verification passed; further output changes are blocked. "
                    "Finish and let independent QA review this candidate.",
                )
            if calls and turn_gate_blocks >= len(calls):
                gate_block_streak += 1
            elif turn_gate_blocks == 0:
                gate_block_streak = 0
            if gate_block_streak in (2, 4):
                loop_alert = (
                    f"Host stage-gate notice: the last {gate_block_streak} turns were ALL intercepted by the "
                    "write/verify stage gate — repeating more probes will be intercepted again and changes "
                    "nothing. read_file stays allowed for one specific file, but ONLY verify_project (with the "
                    "real build/test commands) or browser_observe unlocks the gate. Run one of them now, then "
                    "continue building."
                )
                store.append_event(
                    command.session_id,
                    kind="loop_detected",
                    role=command.role,
                    payload={
                        "turn_seq": turn_seq,
                        "consecutive_identical_turns": gate_block_streak,
                        "tools": ["stage_gate_block"],
                        "guidance_injected": True,
                    },
                )
            store.append_event(
                command.session_id, kind="turn_finished", role=command.role, payload={"turn_seq": turn_seq}
            )
            if verification_passed and command.role == "director":
                # A passing verification is the durable fact that hands the
                # candidate to independent QA. Without this the same Director
                # can keep polishing a candidate that is already verified and
                # the run never reaches its review.
                from polaris.cells.roles.runtime.internal.engineering_roles import (
                    _VERIFICATION_PASSED_GUIDANCE,
                )

                message = {"role": "user", "content": _VERIFICATION_PASSED_GUIDANCE}
                messages.append(message)
                store.append_message(command.session_id, role=command.role, message=message)
            if command.role == "director" and observation.tool == "verify_project" and not observation.ok:
                # Distill the first actionable error so a weak model fixes one
                # concrete location instead of drowning in the full log.
                distilled = _distill_verification_failure(observation.content)
                if distilled:
                    fix_message = {"role": "user", "content": distilled}
                    messages.append(fix_message)
                    store.append_message(command.session_id, role=command.role, message=fix_message)
            if review_reports:
                _verdict, feedback, _index = interpret_review_reports(review_reports)
                return finish("completed", feedback)
    except asyncio.CancelledError:
        cancel.set()
        return finish("cancelled", "Engineering role cancelled after active execution cleanup.")
    except ProcessTreeDrainError as drain_error:
        # Preserve the unresolved started tool and let the physical owner keep
        # the run blocked. An unknown process effect is not a failed test.
        try:
            store.append_event(command.session_id, kind="execution_drain_unproved", role=command.role, payload={})
            finish("interrupted", "Physical execution cleanup could not be proved.")
        except BaseException as journal_error:
            # Reporting failure cannot turn an unproved physical drain into an
            # ordinary exception for which the owner may release its writer.
            raise drain_error from journal_error
        raise
    except Exception as exc:  # noqa: BLE001 - report a provider/tool failure at the role boundary, never retry effects.
        # A bare message name is not auditable: keep a bounded stack so the exact
        # owner line survives in the journal instead of only the text.
        store.append_event(
            command.session_id,
            kind="role_error",
            role=command.role,
            payload={
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=16)[-4000:],
            },
        )
        return finish("failed", f"{type(exc).__name__}: {exc}")
