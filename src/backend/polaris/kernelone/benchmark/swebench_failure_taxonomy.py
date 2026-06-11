"""Deterministic failure-mode labeler for SWE-bench normal-mode sessions.

Zero-LLM: classifies one session from its ``.polaris/runtime/events/*.jsonl``
stream plus the final model patch and the gold patch. Labels are the
measurement substrate for the capability-amplification blueprint — every
Phase 1+ guardrail must move a specific label's frequency, or it didn't work.

Forensics-informed design (2026-06-11 call_id audit):

* ``events.jsonl`` is written BEFORE the console host's snapshot enrichment,
  so its ``tool_result`` payloads are clean ground truth.
* ``tool_call`` events are the batch the model REQUESTED; ``tool_result``
  events and ``complete.batch_receipt.results`` are what actually EXECUTED
  (a mutation-contract retry may replace the batch wholesale). This module
  therefore NEVER pairs tool_call↔tool_result by stream order; requested vs
  executed are aggregated as separate populations.

All text anchors are Polaris's OWN teaching-error formats and guard exception
names (stable platform strings) — never target-project content.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from polaris.kernelone.benchmark.swebench_metrics import patch_files

TAXONOMY_SCHEMA_VERSION = "swebench-failure-taxonomy/1"

# Teaching-error anchors (source: toolkit executor handlers, e.g.
# filesystem.py not-found suggestions; contracts.py parameter teaching).
_NOT_FOUND_RE = re.compile(r"File not found: (.+?)(?:\. Did you mean:|$)")
_DID_YOU_MEAN_RE = re.compile(r"Did you mean: (.+?)\?")
_PARAM_FAIL_ANCHOR = "Parameter validation failed"
# Guard/contract exception anchors as emitted into `error` events.
_MUTATION_GUARD_ANCHOR = "MutationTargetGuardViolation"
_CONTRACT_ANCHOR = "contract_violation"

# Label thresholds — calibrated against run20 (2026-06-11); bump the schema
# version if these change, scores are not comparable across thresholds.
_PATH_HALLUCINATION_MIN_DISTINCT = 3
_MALFORMED_ARGS_MIN = 5
_CONTRACT_PRESSURE_MIN = 3
_DESTRUCTIVE_MIN_REMOVED = 100
_DESTRUCTIVE_MAX_ADD_RATIO = 0.4


def read_events(path: Path) -> list[dict[str, Any]]:
    """Load one events.jsonl stream; malformed lines are skipped, not raised."""
    events: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
    return events


def _iter_executed_payloads(events: Iterable[dict[str, Any]]) -> Iterator[tuple[str, dict[str, Any], bool]]:
    """Yield (tool_name, result_payload, from_receipt) for every EXECUTED call.

    Sources: per-execution ``tool_result`` events, plus call_id-keyed
    ``complete.batch_receipt.results`` items not already seen as a
    ``tool_result`` payload (receipts overlap the event stream; dedup keeps
    occurrence counters honest).
    """
    seen: set[tuple[str, str]] = set()
    receipts: list[tuple[str, dict[str, Any]]] = []
    for event in events:
        etype = event.get("type")
        data = event.get("data") or {}
        if etype == "tool_result":
            tool = str(data.get("tool") or "")
            result = data.get("result")
            if isinstance(result, dict):
                seen.add((tool, _payload_key(result)))
                yield tool, result, False
        elif etype == "complete":
            receipt = data.get("batch_receipt")
            if isinstance(receipt, dict):
                for item in receipt.get("results") or []:
                    if not isinstance(item, dict):
                        continue
                    tool = str(item.get("tool_name") or "")
                    result = item.get("result")
                    if isinstance(result, dict):
                        receipts.append((tool, result))
    for tool, result in receipts:
        if (tool, _payload_key(result)) not in seen:
            yield tool, result, True


def _payload_key(result: dict[str, Any]) -> str:
    return json.dumps(result, sort_keys=True, ensure_ascii=False)[:300]


def _patch_line_stats(diff_text: str) -> dict[str, dict[str, int]]:
    """Per-file added/removed line counts + new-file flags from a git diff."""
    stats: dict[str, dict[str, int]] = {}
    current = ""
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            current = parts[-1][2:] if parts[-1].startswith("b/") else parts[-1]
            stats[current] = {"added": 0, "removed": 0, "new_file": 0}
        elif not current:
            continue
        elif line.startswith("new file mode"):
            stats[current]["new_file"] = 1
        elif line.startswith("+") and not line.startswith("+++"):
            stats[current]["added"] += 1
        elif line.startswith("-") and not line.startswith("---"):
            stats[current]["removed"] += 1
    return stats


def label_session(
    events: list[dict[str, Any]],
    *,
    model_patch: str,
    gold_patch: str,
) -> dict[str, Any]:
    """Classify one session into failure-mode labels + evidence counters."""
    phantom_paths: set[str] = set()
    suggested_paths: set[str] = set()
    phantom_probe_count = 0
    param_validation_failures = 0
    executed_command_count = 0
    executed_total = 0

    for tool, result, _from_receipt in _iter_executed_payloads(events):
        executed_total += 1
        if tool == "execute_command":
            executed_command_count += 1
        error_text = str(result.get("error") or "")
        if not error_text:
            continue
        not_found = _NOT_FOUND_RE.search(error_text)
        if not_found:
            phantom_probe_count += 1
            phantom_paths.add(not_found.group(1).strip().rstrip("."))
        for match in _DID_YOU_MEAN_RE.finditer(error_text):
            for candidate in match.group(1).split(","):
                suggested_paths.add(candidate.strip())
        if _PARAM_FAIL_ANCHOR in error_text:
            param_validation_failures += 1

    requested_command_count = 0
    mutation_guard_violations = 0
    contract_violation_errors = 0
    for event in events:
        etype = event.get("type")
        if etype == "tool_call":
            data = event.get("data") or {}
            if str(data.get("tool") or "") == "execute_command":
                requested_command_count += 1
        elif etype == "error":
            error_text = str(event.get("error") or "")
            if _MUTATION_GUARD_ANCHOR in error_text:
                mutation_guard_violations += 1
            if _CONTRACT_ANCHOR in error_text:
                contract_violation_errors += 1

    model_files = patch_files(model_patch)
    gold_files = patch_files(gold_patch)
    line_stats = _patch_line_stats(model_patch)
    new_files = sorted(path for path, st in line_stats.items() if st["new_file"])
    destructive_files = sorted(
        path
        for path, st in line_stats.items()
        if not st["new_file"]
        and st["removed"] >= _DESTRUCTIVE_MIN_REMOVED
        and st["added"] <= st["removed"] * _DESTRUCTIVE_MAX_ADD_RATIO
    )
    misled_files = sorted((model_files & suggested_paths) - gold_files)

    empty_patch = not model_patch.strip()
    labels: dict[str, bool] = {
        "empty_patch": empty_patch,
        "localization_miss": bool(not empty_patch and gold_files and not (model_files & gold_files)),
        "path_hallucination": len(phantom_paths) >= _PATH_HALLUCINATION_MIN_DISTINCT,
        "suggestion_induced_misedit": bool(misled_files),
        "destructive_overwrite": bool(destructive_files),
        "nonexistent_target_created": bool(set(new_files) - gold_files),
        "malformed_tool_args": param_validation_failures >= _MALFORMED_ARGS_MIN,
        "mutation_contract_pressure": (mutation_guard_violations + contract_violation_errors) >= _CONTRACT_PRESSURE_MIN,
        "no_verification_attempt": requested_command_count == 0 and executed_command_count == 0,
        "verification_suppressed": requested_command_count > 0 and executed_command_count == 0,
    }
    return {
        "schema_version": TAXONOMY_SCHEMA_VERSION,
        "labels": sorted(name for name, on in labels.items() if on),
        "counters": {
            "executed_results": executed_total,
            "executed_command_count": executed_command_count,
            "requested_command_count": requested_command_count,
            "phantom_probe_count": phantom_probe_count,
            "phantom_paths": sorted(phantom_paths),
            "did_you_mean_suggestions": sorted(suggested_paths),
            "param_validation_failures": param_validation_failures,
            "mutation_guard_violations": mutation_guard_violations,
            "contract_violation_errors": contract_violation_errors,
            "new_files_created": new_files,
            "destructive_files": destructive_files,
            "suggestion_misedit_files": misled_files,
        },
    }


def aggregate_labels(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Run-level label frequency table (the before/after comparison surface)."""
    frequency: dict[str, int] = {}
    for record in records:
        for label in record.get("labels", []):
            frequency[label] = frequency.get(label, 0) + 1
    return {
        "schema_version": TAXONOMY_SCHEMA_VERSION,
        "total": len(records),
        "label_frequency": dict(sorted(frequency.items())),
    }
