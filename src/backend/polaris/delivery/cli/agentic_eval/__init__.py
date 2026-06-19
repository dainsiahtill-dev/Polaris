"""One-command deterministic agentic benchmark runner for Polaris CLI.

This command executes ``llm.evaluation`` benchmark cases and emits:
- score and pass/fail status
- failed deterministic checks (root causes)
- tool-call audit summary
- aggregated deterministic repair plan
- UTF-8 JSON audit package persisted under workspace runtime

This package is the lossless decomposition of the former
``agentic_eval.py`` god-command. The original import path
(``polaris.delivery.cli.agentic_eval``) resolves identically: every
public AND privately-imported top-level name from the original module is
re-exported here, and all import-time bindings (CLI helpers, UTF-8 write
plumbing, re-exported suite runners) are preserved.

Module layout:
- ``_coerce``        leaf coercion/normalization helpers (no back-imports)
- ``_probe``         role LLM connectivity probe
- ``_routing``       suite/mode dispatch + level-range normalization
- ``_audit_package`` audit-package builder + formatters/diagnosers
- ``_baseline``      baseline/rerun resolution + score diffing
- ``_render``        human/JSON terminal rendering
- ``_persistence``   UTF-8 runtime JSON writes
- ``_command``       top-level ``run_agentic_eval_command`` orchestrator
"""

from __future__ import annotations

# ── Re-export the standard-library / typing names that were bound at the
# top level of the original single-file module, so the package namespace is
# byte-for-byte equivalent on ``dir()`` / attribute access. ───────────────
import asyncio
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

# ── Re-export the cross-package symbols the original module imported at the
# top level (suite runners, baseline library, kernel filesystem, runtime
# helpers). These were part of the original public surface via ``dir()``. ──
from polaris.cells.llm.evaluation.public.service import (
    list_baseline_library_sources,
    pull_baseline_library,
    run_agentic_benchmark_suite,
    run_context_benchmark_suite,
    run_context_projection_matrix_suite,
    run_projection_adaptive_matrix_suite,
    run_scout_matrix_suite,
    run_speculation_matrix_suite,
    run_strategy_benchmark_suite,
    run_tool_calling_matrix_suite,
)
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs.runtime import KernelFileSystem
from polaris.kernelone.storage import resolve_runtime_path
from polaris.kernelone.utils.time_utils import utc_now_iso

# ── Aggregate every symbol from the decomposed submodules. Each submodule
# declares an explicit ``__all__``; the wildcard imports below therefore
# re-export the full original symbol surface. ─────────────────────────────
from ._audit_package import (
    _build_failure_diagnosis,
    _build_observed_trace,
    _build_transport_observation,
    _check_priority,
    _default_output_path,
    _event_type_histogram,
    _extract_failed_checks,
    _extract_textual_tool_markers,
    _failure_priority,
    _normalize_check_code,
    _priority_label,
    _repair_hint,
    _resolve_runtime_role_bindings,
    _summarize_raw_events,
    _summarize_tool_calls,
    build_agentic_eval_audit_package,
)
from ._baseline import (
    _build_baseline_comparison,
    _extract_failed_case_ids,
    _extract_failed_check_codes,
    _read_json_file,
    _resolve_baseline_audit_path,
    _resolve_rerun_audit_path,
)
from ._coerce import (
    _CHECK_CATEGORY_ORDER,
    _as_dict,
    _as_list,
    _format_counter,
    _normalise_case_ids,
    _normalize_matrix_transport,
    _normalize_tokens,
    _to_float,
    _to_int,
    _to_percent,
    _truncate_text,
)
from ._command import run_agentic_eval_command
from ._persistence import _persist_audit_package, _persist_runtime_json
from ._probe import (
    _ALL_PROBE_ROLES,
    _DEFAULT_PROBE_TIMEOUT,
    _PROBE_MESSAGE,
    _print_probe_human,
    _probe_role,
    _run_probe_async,
    run_probe,
)
from ._render import (
    _build_progress_callback,
    _print_baseline_pull_human,
    _print_human,
    _render_progress_bar,
    _report_context_projection_matrix,
    _report_projection_adaptive_matrix,
    _report_speculation_matrix,
)
from ._routing import (
    _TOOL_CALLING_MATRIX_LEVEL_PREFIXES,
    _aggregate_all_mode_results,
    _expand_level_range_to_case_ids,
    _normalize_suite_name,
    _parse_level_range,
    _run_benchmark_by_mode,
    _suite_runners,
)

# Preserve the original module's public ``__all__`` exactly. The original
# single-file module exported only these two names; the remaining symbols
# stay importable as attributes (matching the original namespace) but are
# not part of the curated ``*`` surface.
__all__ = [
    "build_agentic_eval_audit_package",
    "run_agentic_eval_command",
]

if TYPE_CHECKING:
    import argparse as argparse
