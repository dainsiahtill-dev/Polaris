"""Deterministic SWE-bench scoring metrics (capability-amplification Phase 0).

Pure functions — no I/O, no LLM. Shared by the normal-mode harness script
(``scripts/swebench/swebench_normal_mode.py``) and future agentic-eval suites.

Three measurement layers on top of the official harness's strict ``resolved``:

1. ``pure_f2p_resolved`` — flakiness shield: a patch is functionally correct
   when every FAIL_TO_PASS test passes, independent of env-flaky PASS_TO_PASS
   failures (ported from ``arch_b_converge`` Task 2; WSL2 network-type P2P
   tests fail even with the gold patch applied).
2. Gold file/hunk overlap — partial credit so harness improvements stay
   measurable below the resolved threshold (the run20 0/17 zero-score wall).
   Both the model patch and the gold patch diff against the same base commit,
   so old-file line coordinates are directly comparable.
3. ``SCORE_SCHEMA_VERSION`` — score-scale version stamp persisted in every
   record so later rescoring never silently mixes incomparable scales.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SCORE_SCHEMA_VERSION = "swebench-score/1"
PAIRED_SCHEMA_VERSION = "swebench-paired/1"

_DIFF_GIT_RE = re.compile(r"^diff --git a/(?P<old>.+?) b/(?P<new>.+)$")
_HUNK_RE = re.compile(r"^@@ -(?P<start>\d+)(?:,(?P<length>\d+))? \+\d+(?:,\d+)? @@")


@dataclass(frozen=True)
class PatchHunk:
    """One hunk of a unified diff, in old-file (base-commit) coordinates."""

    path: str
    old_start: int
    old_length: int

    def interval(self, slack: int = 0) -> tuple[int, int]:
        """Closed line interval covered by this hunk, expanded by ``slack``."""
        start = self.old_start
        end = self.old_start + max(self.old_length, 1) - 1
        return (max(start - slack, 0), end + slack)


def parse_patch_hunks(diff_text: str) -> list[PatchHunk]:
    """Parse a git-style unified diff into per-file hunks.

    Tolerates arbitrary prose around the diff: only ``diff --git`` headers and
    ``@@`` hunk markers are interpreted. Hunks appearing before any file
    header are ignored (malformed input is scored, never raised on).
    """
    hunks: list[PatchHunk] = []
    current_path = ""
    for line in diff_text.splitlines():
        header = _DIFF_GIT_RE.match(line)
        if header:
            current_path = header.group("new")
            continue
        if not current_path:
            continue
        hunk = _HUNK_RE.match(line)
        if hunk:
            length = hunk.group("length")
            hunks.append(
                PatchHunk(
                    path=current_path,
                    old_start=int(hunk.group("start")),
                    old_length=int(length) if length is not None else 1,
                )
            )
    return hunks


def patch_files(diff_text: str) -> set[str]:
    """Set of (new-side) file paths a git-style diff touches."""
    files: set[str] = set()
    for line in diff_text.splitlines():
        header = _DIFF_GIT_RE.match(line)
        if header:
            files.add(header.group("new"))
    return files


def gold_file_metrics(model_patch: str, gold_patch: str) -> dict[str, Any]:
    """File-level localization credit of ``model_patch`` against the gold patch.

    ``gold_file_hit`` is the binary "touched at least one gold file" signal;
    ``gold_file_recall`` is the fraction of gold files touched. Empty inputs
    score 0 — a missing patch earns no credit and raises no error.
    """
    gold_files = patch_files(gold_patch)
    model_files = patch_files(model_patch)
    overlap = gold_files & model_files
    recall = (len(overlap) / len(gold_files)) if gold_files else 0.0
    return {
        "gold_files": sorted(gold_files),
        "model_files": sorted(model_files),
        "gold_file_hit": bool(overlap),
        "gold_file_recall": recall,
    }


def gold_hunk_overlap(model_patch: str, gold_patch: str, slack: int = 0) -> float:
    """Fraction of gold hunks intersected by a model hunk in the same file.

    Intervals are compared in old-file coordinates (both diffs share the base
    commit). ``slack`` expands every hunk interval symmetrically so near-miss
    edits (right function, slightly off lines) can earn partial credit.
    Returns 0.0 when the gold patch has no hunks.
    """
    gold_hunks = parse_patch_hunks(gold_patch)
    if not gold_hunks:
        return 0.0
    model_by_file: dict[str, list[tuple[int, int]]] = {}
    for hunk in parse_patch_hunks(model_patch):
        model_by_file.setdefault(hunk.path, []).append(hunk.interval(slack))
    matched = 0
    for gold in gold_hunks:
        g_start, g_end = gold.interval(slack)
        for m_start, m_end in model_by_file.get(gold.path, []):
            if m_start <= g_end and g_start <= m_end:
                matched += 1
                break
    return matched / len(gold_hunks)


def pure_f2p_resolved(report: Mapping[str, Any]) -> bool:
    """Flakiness-shielded functional success from an official-harness report.

    True when the patch applied and ALL target FAIL_TO_PASS tests pass (at
    least one), regardless of PASS_TO_PASS failures — those are env-flaky
    (network) or out-of-scope on this host and poison strict ``resolved``.
    """
    tests_status = report.get("tests_status") or {}
    f2p = tests_status.get("FAIL_TO_PASS") or {}
    failures = list(f2p.get("failure") or [])
    successes = list(f2p.get("success") or [])
    applied = bool(report.get("patch_successfully_applied"))
    return applied and not failures and len(successes) > 0


def build_score_record(
    *,
    instance_id: str,
    model_patch: str,
    gold_patch: str,
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Assemble the full per-instance score record (schema-stamped).

    ``report`` is the per-instance dict from the official harness's
    ``report.json`` (may be empty when the harness produced no report — the
    record then carries patch-derived metrics only).
    """
    tests_status = report.get("tests_status") or {}
    f2p = tests_status.get("FAIL_TO_PASS") or {}
    p2p = tests_status.get("PASS_TO_PASS") or {}
    record: dict[str, Any] = {
        "schema_version": SCORE_SCHEMA_VERSION,
        "instance_id": instance_id,
        "resolved": bool(report.get("resolved")),
        "patch_applied": bool(report.get("patch_successfully_applied")),
        "pure_f2p_resolved": pure_f2p_resolved(report),
        "f2p_pass_count": len(list(f2p.get("success") or [])),
        "f2p_fail": list(f2p.get("failure") or []),
        "p2p_fail_count": len(list(p2p.get("failure") or [])),
        "empty_patch": not model_patch.strip(),
        "patch_lines": model_patch.count("\n"),
        "gold_hunk_overlap": gold_hunk_overlap(model_patch, gold_patch),
        "gold_hunk_overlap_slack10": gold_hunk_overlap(model_patch, gold_patch, slack=10),
    }
    record.update(gold_file_metrics(model_patch, gold_patch))
    return record


def aggregate_score_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Run-level aggregate over per-instance score records."""
    total = len(records)

    def _rate(key: str) -> float:
        return (sum(1 for r in records if r.get(key)) / total) if total else 0.0

    def _mean(key: str) -> float:
        return (sum(float(r.get(key, 0.0)) for r in records) / total) if total else 0.0

    return {
        "schema_version": SCORE_SCHEMA_VERSION,
        "total": total,
        "resolved": sum(1 for r in records if r.get("resolved")),
        "pure_f2p_resolved": sum(1 for r in records if r.get("pure_f2p_resolved")),
        "resolved_rate": _rate("resolved"),
        "pure_f2p_resolved_rate": _rate("pure_f2p_resolved"),
        "gold_file_hit_rate": _rate("gold_file_hit"),
        "mean_gold_file_recall": _mean("gold_file_recall"),
        "mean_gold_hunk_overlap": _mean("gold_hunk_overlap"),
        "mean_gold_hunk_overlap_slack10": _mean("gold_hunk_overlap_slack10"),
        "non_empty_patches": sum(1 for r in records if not r.get("empty_patch")),
    }


_PAIRED_METRIC_KEYS = ("resolved", "pure_f2p_resolved", "gold_file_hit", "gold_hunk_overlap")


def _arm_summary(repeats: list[list[dict[str, Any]]]) -> dict[str, Any]:
    per_repeat = [aggregate_score_records(run) for run in repeats]

    def _mean(key: str) -> float:
        return (sum(float(p[key]) for p in per_repeat) / len(per_repeat)) if per_repeat else 0.0

    return {
        "repeats": len(per_repeat),
        "per_repeat": per_repeat,
        "mean_resolved_rate": _mean("resolved_rate"),
        "mean_pure_f2p_resolved_rate": _mean("pure_f2p_resolved_rate"),
        "mean_gold_file_hit_rate": _mean("gold_file_hit_rate"),
        "mean_gold_hunk_overlap": _mean("mean_gold_hunk_overlap"),
    }


def _per_instance_means(repeats: list[list[dict[str, Any]]]) -> dict[str, dict[str, float]]:
    samples: dict[str, dict[str, list[float]]] = {}
    for run in repeats:
        for record in run:
            slot = samples.setdefault(str(record["instance_id"]), {key: [] for key in _PAIRED_METRIC_KEYS})
            for key in _PAIRED_METRIC_KEYS:
                slot[key].append(
                    float(bool(record.get(key))) if key != "gold_hunk_overlap" else float(record.get(key, 0.0))
                )
    return {
        iid: {key: (sum(values) / len(values) if values else 0.0) for key, values in slots.items()}
        for iid, slots in samples.items()
    }


def build_paired_report(
    label_a: str,
    repeats_a: list[list[dict[str, Any]]],
    label_b: str,
    repeats_b: list[list[dict[str, Any]]],
) -> dict[str, Any]:
    """Paired two-arm comparison report over per-instance score records.

    Each arm is a list of repeats; each repeat is the ``records`` list of one
    full run over the SAME pinned subset. Per-instance metrics are averaged
    across repeats, then differenced (arm B − arm A) on the instances both
    arms covered — this is the weak-vs-strong (or before-vs-after) surface
    of the capability-amplification north-star metric.
    """
    instances_a = _per_instance_means(repeats_a)
    instances_b = _per_instance_means(repeats_b)
    shared = sorted(set(instances_a) & set(instances_b))
    paired_delta = {
        iid: {key: instances_b[iid][key] - instances_a[iid][key] for key in _PAIRED_METRIC_KEYS} for iid in shared
    }
    delta_summary = {
        key: (sum(delta[key] for delta in paired_delta.values()) / len(paired_delta)) if paired_delta else 0.0
        for key in _PAIRED_METRIC_KEYS
    }
    return {
        "schema_version": PAIRED_SCHEMA_VERSION,
        "arm_labels": [label_a, label_b],
        "arms": {label_a: _arm_summary(repeats_a), label_b: _arm_summary(repeats_b)},
        "per_instance": {label_a: instances_a, label_b: instances_b},
        "shared_instances": shared,
        "paired_delta": paired_delta,
        "delta_summary_b_minus_a": delta_summary,
    }
