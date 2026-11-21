"""One native-tool loop for each professional role; no model-maintained protocol."""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import json
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, cast

from polaris.cells.roles.runtime.internal.engineering_acceptance import ClauseFinding, require_observed_clause_evidence
from polaris.cells.roles.runtime.internal.engineering_execution_policy import resolve_execution_policy
from polaris.cells.roles.runtime.internal.engineering_roles import register_consult_tool, role_instruction, role_scope
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools, bounded_output
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
_WORKING_SET_TOKENS_DEFAULT = 32000
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
) -> list[dict[str, Any]]:
    """Build the request messages inside the model's context window.

    A native tool working set has no budget: it grows with every turn until
    the provider window refuses the whole request. The history is therefore
    bounded before the request is built, by dropping whole older turns. The
    durable journal keeps every observation, so nothing is lost.
    """
    from polaris.cells.roles.runtime.internal.engineering_file_card_projection import (
        fold_file_card_previews,
        folded_card_projections,
    )

    messages = fold_file_card_previews(
        messages,
        semantic_pins=semantic_runtime.pin_reasons() if semantic_runtime is not None else None,
        semantic_summaries=semantic_runtime.fold_summaries() if semantic_runtime is not None else None,
        inactive_fold_activities=fold_window,
    )
    prior_fold_refs = {
        str(event["payload"].get("projection_ref"))
        for event in store.load_events(command.session_id, kinds=("context_projection_folded",), role=role)
        if event["payload"].get("projection_ref")
    }
    for projection in folded_card_projections(messages):
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

    def build(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return build_visual_messages(
            history,
            events=events,
            role=role,
            binding_provider=visual_binding,
            audit=audit,
        )

    window = int(context_window_tokens or 0)
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
    from polaris.kernelone.llm.engine.token_estimator import TokenEstimator

    # Align compression accounting with admission accounting. The default
    # compressor scores message content as plain text, so base64 image bytes
    # embedded in browser_observation JSON are counted as text and no budget
    # can ever fit a visual history (run 6e61f355 died this way). This cost
    # strips data_base64 payloads for the text side and prices the stripped
    # bytes separately, matching the conservative serialization proxy.
    _data_base64_pattern = re.compile(r'("data_base64"\s*:\s*")[^"]*(")')

    def _visual_aligned_cost(message: Mapping[str, Any]) -> int:
        content = message.get("content")
        extra = 0
        if isinstance(content, str):
            stripped, count = _data_base64_pattern.subn(r"\1\2", content)
            if count:
                removed = sum(len(match.group(0)) for match in _data_base64_pattern.finditer(content))
                extra = TokenEstimator.estimate("x" * min(removed, 4_000_000))
            text = stripped
        elif content is None:
            text = ""
        else:
            text = json.dumps(content, ensure_ascii=False)
        if message.get("tool_calls"):
            text = f"{text}{json.dumps(message['tool_calls'], ensure_ascii=False)}"
        return TokenEstimator.estimate(text) + extra + 8

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
            bounded = compress_native_tool_history(messages, attempt_budget, message_cost=_visual_aligned_cost)
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
    raise ValueError("required_visual_input_budget_exceeded: the trimmed history still does not fit the model window")


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
            payload.get("tool") == "read_file"
            or (payload.get("tool") == "repo_rg" and bool(str(payload.get("content", "")).strip()))
        )
    )


def _loop_recovery_instruction(role: str, enabled: frozenset[str]) -> str:
    if role == "qa" and "report_review" in enabled:
        return (
            "Submit report_review using exact current observation call IDs for passing clauses. "
            "Record observed defects as fail and unavailable or inconclusive evidence as unknown. "
            "If an unresolved clause still needs a check, use a different authorized read-only observation."
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

    def resolve_file_card(reference: str) -> dict[str, Any]:
        for event in reversed(store.load_events(command.session_id, kinds=("tool_observed",))):
            payload = event["payload"]
            if payload.get("tool") != "read_file" or payload.get("ok") is not True:
                continue
            try:
                card = json.loads(str(payload.get("content") or "{}")).get("file_card")
            except json.JSONDecodeError:
                continue
            if isinstance(card, dict) and card.get("content_ref") == reference:
                return card
        raise ValueError(f"unknown file_card reference: {reference}")

    from polaris.cells.roles.runtime.internal.engineering_semantic_runtime import (
        facts_for_advice,
        semantic_runtime_from_environment,
    )

    semantic_runtime = semantic_runtime_from_environment()
    from polaris.cells.roles.runtime.internal.engineering_epoch_kimi import kimi_epoch_pool_from_environment
    from polaris.cells.roles.runtime.internal.engineering_epoch_runtime import EpochRuntime, facts_for_epoch

    epoch_pool = kimi_epoch_pool_from_environment()
    fallback_pool = semantic_runtime.pool if semantic_runtime is not None else None
    epoch_runtime = (
        EpochRuntime(epoch_pool or fallback_pool) if epoch_pool is not None or fallback_pool is not None else None
    )
    semantic_reported: set[str] = set()
    epoch_reported: set[str] = set()
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
    if not messages:
        messages = [
            {
                "role": "system",
                "content": role_instruction(
                    command.role,
                    visual="browser_observe" in tools.enabled,
                    observation_mode=scope.browser.observation_mode if scope.browser else "images",
                    require_visual_review=bool(scope.browser and scope.browser.require_visual_review),
                    require_qa=bool(store.load_run(command.session_id, workspace=command.workspace)["require_qa"]),
                ),
            },
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
    first_verification_fraction = float(
        os.environ.get(_FIRST_VERIFICATION_FRACTION_ENV, str(_FIRST_VERIFICATION_FRACTION_DEFAULT)) or 0
    )
    qa_turn_limit = int(os.environ.get(_QA_TURN_LIMIT_ENV, str(_QA_TURN_LIMIT_DEFAULT)) or 0)
    qa_turns = 0
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
            run_now = store.load_run(command.session_id, workspace=command.workspace)
            if semantic_runtime is not None:
                advice = semantic_runtime.poll()
                advice_fingerprint = str(advice.get("fingerprint") or "") if advice is not None else ""
                if advice is not None and advice_fingerprint not in semantic_reported:
                    store.append_event(
                        command.session_id,
                        kind="semantic_advice_completed",
                        role=command.role,
                        payload=advice,
                    )
                    semantic_reported.add(advice_fingerprint)
                advice_request, advice_refs, advice_fingerprint = facts_for_advice(
                    run_now, store.load_events(command.session_id)
                )
                if advice_refs.node_ids:
                    semantic_runtime.schedule(advice_request, advice_refs, fingerprint=advice_fingerprint)
            if epoch_runtime is not None:
                epoch_card = epoch_runtime.poll()
                if epoch_card is not None and epoch_card["fingerprint"] not in epoch_reported:
                    store.append_event(
                        command.session_id,
                        kind="epoch_card_completed",
                        role="devourer",
                        payload=epoch_card,
                    )
                    epoch_reported.add(epoch_card["fingerprint"])
                epoch_request, epoch_refs, epoch_fingerprint = facts_for_epoch(
                    run_now, store.load_events(command.session_id)
                )
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
            visual_projection: dict[str, Any] = {}
            from polaris.cells.roles.runtime.internal.engineering_context_budget import context_fold_window
            from polaris.kernelone.llm.engine.token_estimator import TokenEstimator

            fold_window = context_fold_window(
                limits=limits,
                created_at=float(run_now["created_at"]),
                current_tokens=TokenEstimator.estimate(json.dumps(messages, ensure_ascii=False)),
                context_window_tokens=context_window_tokens,
            )
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
            )
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

                run, state_events = store.load_projection_inputs(
                    command.session_id, workspace=command.workspace, kinds=STATE_EVENT_KINDS
                )
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
            if host_parts:
                options["messages"].append({"role": "user", "content": "\n".join(host_parts)})
            qa_report_required = False
            if command.role == "qa":
                from polaris.cells.roles.runtime.internal.engineering_working_state import (
                    QA_WORKING_STATE_EVENT_KINDS,
                    qa_working_state_message,
                )

                run, working_events = store.load_projection_inputs(
                    command.session_id, workspace=command.workspace, kinds=QA_WORKING_STATE_EVENT_KINDS
                )
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
                        instruction = (
                            f"Current QA source receipt ID: {source}. Current host check IDs: "
                            + json.dumps([check["call_id"] for check in run["checks"] if check.get("ok") is True])
                            + ". In each passing finding copy a supporting call ID into evidence; "
                            "put the explanation in feedback. Mark unverifiable clauses unknown."
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
                        },
                    )
            from polaris.cells.roles.runtime.internal.engineering_phase_policy import phase_for

            phase = phase_for(
                command.role,
                verification=str(run_now.get("verification") or "not_run"),
                first_verification_due=first_verification_due,
                preparation_pending=preparation_pending,
                qa_report_required=qa_report_required,
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
                store.record_tool_dispatch(
                    command.session_id,
                    turn_seq=turn_seq,
                    call_id=call.id,
                    tool=canonical,
                    arguments=normalized,
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
                            if preparation_pending and command.role == "director" and canonical == "verify_project":
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
                                and canonical in {"write_file", "edit_file", "execute_command"}
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
                            and bool(
                                tools.scope.write_roots
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
                if advances_revision and observation.tool == "execute_command":
                    # An actual writing preparation step clears the EROFS gate.
                    preparation_pending = False
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
        store.append_event(
            command.session_id,
            kind="role_error",
            role=command.role,
            payload={"error_type": type(exc).__name__, "error": str(exc)},
        )
        return finish("failed", f"{type(exc).__name__}: {exc}")
