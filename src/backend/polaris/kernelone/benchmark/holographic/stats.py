"""Statistical calculations and small helpers for holographic benchmarks."""

from __future__ import annotations

import json
import math
import random
import re
import time
from datetime import datetime, timezone
from typing import Any

from polaris.kernelone.akashic.knowledge_pipeline.protocols import SemanticChunk


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _perf_ms(start_ns: int) -> float:
    return (time.perf_counter_ns() - start_ns) / 1_000_000.0


def _seed_random() -> None:
    random.seed(42)


def _evaluate_thresholds(metrics: dict[str, Any], thresholds: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for threshold_name, threshold_value in thresholds.items():
        if threshold_name.endswith("_lt"):
            metric_name = threshold_name[:-3]
            actual = metrics.get(metric_name)
            if actual is None or float(actual) >= float(threshold_value):
                failures.append(f"{metric_name} expected < {threshold_value}, got {actual}")
        elif threshold_name.endswith("_gt"):
            metric_name = threshold_name[:-3]
            actual = metrics.get(metric_name)
            if actual is None or float(actual) <= float(threshold_value):
                failures.append(f"{metric_name} expected > {threshold_value}, got {actual}")
        elif threshold_name.endswith("_eq"):
            metric_name = threshold_name[:-3]
            actual = metrics.get(metric_name)
            if actual is None:
                failures.append(f"{metric_name} expected == {threshold_value}, got None")
            else:
                expected = float(threshold_value)
                if not math.isclose(float(actual), expected, rel_tol=1e-9, abs_tol=1e-9):
                    failures.append(f"{metric_name} expected == {expected}, got {actual}")
    return failures


def _serialized_json(value: dict[str, Any] | None) -> str:
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _contains_redacted(payload: dict[str, Any]) -> bool:
    serialized = json.dumps(payload, ensure_ascii=False)
    return "[REDACTED]" in serialized


def _python_block_ranges(text: str, *, block_type: str) -> list[tuple[int, int]]:
    lines = text.splitlines()
    starts: list[int] = []
    for index, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if (block_type == "function" and stripped.startswith("def ")) or (
            block_type == "class" and stripped.startswith("class ")
        ):
            starts.append(index)
    ranges: list[tuple[int, int]] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] - 1 if idx + 1 < len(starts) else len(lines)
        ranges.append((start, end))
    return ranges


def _chunk_ranges_from_semantic(chunks: list[SemanticChunk]) -> list[tuple[int, int]]:
    return [(chunk.line_start, chunk.line_end) for chunk in chunks]


def _chunk_ranges_fixed_80(total_lines: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    line = 1
    while line <= total_lines:
        end = min(total_lines, line + 79)
        ranges.append((line, end))
        line += 80
    return ranges


def _boundary_retention(blocks: list[tuple[int, int]], chunks: list[tuple[int, int]]) -> float:
    if not blocks:
        return 100.0
    kept = 0
    for block_start, block_end in blocks:
        if any(chunk_start <= block_start and chunk_end >= block_end for chunk_start, chunk_end in chunks):
            kept += 1
    return (kept / len(blocks)) * 100.0


def _token_similarity(left: str, right: str) -> float:
    token_pattern = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    left_tokens = set(token_pattern.findall(left))
    right_tokens = set(token_pattern.findall(right))
    if not left_tokens and not right_tokens:
        return 1.0
    union = left_tokens | right_tokens
    if not union:
        return 1.0
    return len(left_tokens & right_tokens) / len(union)
