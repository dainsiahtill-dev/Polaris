"""Read-only continuation projection from canonical committed turn facts.

Opaque references carry no execution permission. Every consumer re-reads the
original workspace/run/task/session facts with strict integrity; native history
and caller-projected result rows never authorize completion.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.cells.events.fact_stream.public import (
    QueryFactEventsV1,
    QueryPayloadFactEventsV1,
    QuerySegmentedFactLedgerHeadV1,
    query_fact_events,
    query_payload_fact_events,
    query_payload_fact_ledger_presence,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name
from polaris.kernelone.tools.tool_kinds import ACTIVE_WRITE_TOOLS

CONTINUATION_EVIDENCE_KEY = "_kernel_task_continuation_evidence"
_STREAM = "roles.kernel.turn_outcomes"
_PAYLOAD_STREAM = "roles.kernel.turn_outcomes.v3"
_MINT = object()


@dataclass(frozen=True, slots=True)
class ContinuationReceiptReferences:
    session_id: str
    references: tuple[tuple[str, str, int, str], ...]
    _owner: object
    streams: tuple[str, ...] = ()

    @classmethod
    def from_receipts(cls, session_id: str, receipts: Sequence[Mapping[str, Any]]) -> ContinuationReceiptReferences:
        references = []
        streams = []
        for receipt in receipts:
            if not (
                receipt.get("validation_passed") is True and receipt.get("fact_stream") in {_STREAM, _PAYLOAD_STREAM}
            ):
                raise RuntimeError("continuation_commit_locator_invalid")
            turn_id = str(receipt.get("turn_id") or "")
            event_id = str(receipt.get("fact_event_id") or "")
            sequence = receipt.get("fact_event_seq")
            outcome_hash = str(receipt.get("outcome_hash") or "")
            if not turn_id or not event_id or type(sequence) is not int or sequence <= 0 or len(outcome_hash) != 64:
                raise RuntimeError("continuation_commit_locator_invalid")
            references.append((turn_id, event_id, sequence, outcome_hash))
            streams.append(str(receipt["fact_stream"]))
        if not session_id or not references or len(set(references)) != len(references):
            raise RuntimeError("continuation_commit_locator_invalid")
        return cls(session_id, tuple(references), _MINT, tuple(streams))


def capture_task_source_state(workspace: str, source_targets: Sequence[str]) -> dict[str, str]:
    """Hash the CE-declared source set through KFS without creating files."""
    filesystem = KernelFileSystem(workspace, get_default_adapter())
    state = {}
    for target in sorted(set(source_targets)):
        logical = str(target).replace("\\", "/")
        while logical.startswith("./"):
            logical = logical[2:]
        if (
            not logical
            or Path(logical).is_absolute()
            or ".." in logical.split("/")
            or logical.startswith(("runtime/", ".polaris/"))
        ):
            raise ValueError("continuation_source_target_invalid")
        try:
            if filesystem.workspace_is_dir(logical):
                # A permission root is not an exact verifier input. Never
                # enumerate it or use directory presence as source freshness.
                continue
            state[logical] = hashlib.sha256(filesystem.workspace_read_bytes(logical)).hexdigest()
        except FileNotFoundError:
            state[logical] = "missing"
    return state


def load_committed_continuation_rows(
    evidence: Any,
    *,
    workspace: str,
    run_id: str,
    task_id: str,
    session_id: str,
    source_targets: Sequence[str],
) -> list[dict[str, Any]]:
    """Resolve exact commit locators and project latest source-bound gate results."""
    if type(evidence) is not ContinuationReceiptReferences or evidence._owner is not _MINT:
        raise RuntimeError("continuation_evidence_not_kernel_owned")
    if evidence.session_id != session_id or not workspace or not run_id or not task_id or not session_id:
        raise RuntimeError("continuation_identity_mismatch")
    if len(evidence.streams) != len(evidence.references) or len(set(evidence.streams)) != 1:
        raise RuntimeError("continuation_commit_protocol_ambiguous")
    stream = evidence.streams[0]
    if stream == _PAYLOAD_STREAM and not query_payload_fact_ledger_presence(
        QuerySegmentedFactLedgerHeadV1(workspace=workspace, logical_stream=stream)
    ):
        raise RuntimeError("continuation_identity_mismatch")
    expected = {reference[1]: reference for reference in evidence.references}
    selected: dict[str, dict[str, Any]] = {}
    offset = 0
    continuation = None
    while True:
        if stream == _STREAM:
            queried = query_fact_events(
                QueryFactEventsV1(
                    workspace=workspace,
                    stream=stream,
                    run_id=run_id,
                    task_id=task_id,
                    limit=1000,
                    offset=offset,
                    strict_integrity=True,
                )
            )
            events = queried.events
        else:
            page = query_payload_fact_events(
                QueryPayloadFactEventsV1(workspace=workspace, logical_stream=stream, limit=8, continuation=continuation)
            )
            events = page.events
        for event in events:
            reference = expected.get(str(event.get("event_id") or ""))
            if reference is None:
                continue
            # Expanded content is separately authenticated by the payload owner;
            # the small sealed record keeps its actual stream/global sequence.
            payload = event.get("payload") if stream == _STREAM else event.get("expanded_payload")
            sequence = event.get("seq") if stream == _STREAM else event.get("global_seq")
            if not isinstance(payload, Mapping):
                raise RuntimeError("continuation_fact_invalid")
            provenance = payload.get("provenance")
            binding = payload.get("session_binding")
            outcome = payload.get("outcome")
            if (
                not isinstance(provenance, Mapping)
                or not isinstance(binding, Mapping)
                or not isinstance(outcome, Mapping)
            ):
                raise RuntimeError("continuation_identity_mismatch")
            digest = hashlib.sha256(
                json.dumps(dict(outcome), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if (
                event.get("source") != "roles.kernel.transaction"
                or event.get("event_type") != "turn_outcome_committed"
                or sequence != reference[2]
                or payload.get("schema_version")
                != ("roles.kernel.turn_outcome_fact.v2" if stream == _STREAM else "roles.kernel.turn_outcome_fact.v3")
                or payload.get("outcome_hash") != reference[3]
                or digest != reference[3]
                or provenance.get("workspace") != str(Path(workspace).resolve())
                or provenance.get("run_id") != run_id
                or provenance.get("task_id") != task_id
                or provenance.get("turn_id") != reference[0]
                or binding.get("session_id") != session_id
            ):
                raise RuntimeError("continuation_identity_mismatch")
            if reference[1] in selected:
                raise RuntimeError("continuation_commit_locator_ambiguous")
            selected[reference[1]] = dict(payload)
        if stream == _STREAM:
            if not queried.next_offset:
                break
            offset = queried.next_offset
        else:
            if page.continuation is None:
                break
            continuation = page.continuation
    if set(selected) != set(expected):
        raise RuntimeError("continuation_identity_mismatch")
    current_state = capture_task_source_state(workspace, source_targets)
    rows: list[dict[str, Any]] = []
    for reference in sorted(evidence.references, key=lambda item: item[2]):
        payload = selected[reference[1]]
        execution = payload["outcome"].get("execution")
        execution = execution if isinstance(execution, Mapping) else {}
        receipt = execution.get("receipt")
        receipt = receipt if isinstance(receipt, Mapping) else {}
        invocations = {
            str(item.get("call_id") or ""): item
            for item in execution.get("invocations", [])
            if isinstance(item, Mapping)
        }
        binding = payload["session_binding"]
        source_fresh = bool(current_state) and binding.get("source_state_before") == current_state == binding.get(
            "source_state_after"
        )
        for raw in receipt.get("results", []):
            if not isinstance(raw, Mapping):
                continue
            row = dict(raw)
            invocation = invocations.get(str(row.get("call_id") or ""), {})
            row["arguments"] = dict(invocation.get("arguments") or {})
            name = str(normalize_tool_name(str(row.get("tool_name") or "")) or row.get("tool_name") or "")
            row["tool_name"] = name
            row["tool"] = name
            row["success"] = row.get("status") == "success"
            if name in ACTIVE_WRITE_TOOLS and row["success"] and isinstance(row.get("effect_receipt"), Mapping):
                # All earlier verification results are invalidated by a new mutation.
                rows = [item for item in rows if item.get("tool_name") != "execute_command"]
            if name == "execute_command":
                command = str(row["arguments"].get("command") or "")
                rows = [
                    item
                    for item in rows
                    if not (item.get("tool_name") == name and item.get("arguments", {}).get("command") == command)
                ]
                if not source_fresh:
                    row["success"] = False
                    row["status"] = "error"
                    row["error"] = "verification_evidence_stale"
            rows.append(row)
    return rows
