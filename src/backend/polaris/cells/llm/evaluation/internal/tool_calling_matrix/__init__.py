"""Deterministic tool-calling matrix suite for industrial agent evaluation.

This module provides a comprehensive tool-calling evaluation system that tests
agent behavior across multiple dimensions: tooling correctness, safety compliance,
contract adherence, and evidence collection. Each test case defines expected
tool usage patterns, argument constraints, output requirements, and parity
requirements between streaming and non-streaming modes.

Architecture
------------
The tool-calling matrix operates in four phases:

1. **Case Loading**: Loads matrix cases from JSON fixtures
2. **Observation Collection**: Runs sessions in both stream and non-stream modes
3. **Deterministic Checking**: Validates observations against case-defined rules
4. **Scoring**: Computes weighted scores across four categories

Scoring Categories
------------------
- tooling (35%): Tool selection, ordering, and count correctness
- safety (30%): Forbidden tools, required refusals, sensitive operations
- contract (20%): Argument types, unknown args, output substrings
- evidence (15%): Argument values, required presence, array contents

Module layout
-------------
This import path is a package; behavior is identical to the prior single-module
form. Symbols are split across private submodules and re-exported here losslessly:

- ``_contracts``: frozen dataclasses, executor Protocol, value-coercion helpers,
  shared constants.
- ``_loading``: case loading from JSON fixtures + fixture-root constants.
- ``_prompt_contract``: prompt-contract composition + tool-token/arg
  canonicalization.
- ``_judge``: deterministic judging engine.
- ``_runner``: async observation collectors + top-level runner.
"""

from __future__ import annotations

# Re-exported module-level imports preserved from the original single-module
# form so that ``from ...tool_calling_matrix import <name>`` keeps resolving the
# same names (lossless surface). These are the standard-library / typing /
# cross-cell symbols that were bound at module top level.
import json
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleSessionCommandV1,
    RoleExecutionResultV1,
)
from polaris.kernelone.storage import resolve_runtime_path
from polaris.kernelone.tool_execution.contracts import canonicalize_tool_name

from ..benchmark_loader import build_case_sandbox_key, copy_fixture_tree
from ..utils import new_test_run_id, utc_now, write_json_atomic
from ._contracts import (
    _EDIT_TOOL_EQUIVALENCE,
    _REFUSAL_MARKERS,
    _SCORE_WEIGHTS,
    MATRIX_TOOL_EQUIVALENCE_GROUPS,
    MatrixJudgeCheck,
    MatrixJudgeVerdict,
    MatrixObservation,
    RoleSessionMatrixExecutor,
    ToolCallingMatrixCase,
    _history_entries,
    _mapping_dict,
    _non_empty,
    _normalize_case_ids,
    _sanitize_json,
    _to_float,
    _to_int,
    _tuple_of_strings,
)
from ._judge import (
    _category_score,
    _check_mode,
    _check_parity,
    _failed_check_summary,
    _first_tool_call,
    _judge_case,
    _known_arg_keys,
    _value_matches_type,
)
from ._loading import (
    CASES_ROOT,
    FIXTURES_ROOT,
    WORKSPACES_ROOT,
    load_builtin_tool_calling_matrix_cases,
    load_tool_calling_matrix_case,
    materialize_case_workspace,
    resolve_case_fixture_dir,
)
from ._prompt_contract import (
    _canonical_tool_tokens,
    _canonicalize_judge_arg_keys,
    _compose_case_prompt,
    _compose_stream_retry_prompt_for_under_calls,
    _event_value,
    _format_ordered_groups,
    _normalize_judge_args,
    _normalize_tool_calls,
)
from ._runner import (
    RolesRuntimeMatrixExecutor,
    _artifact_path,
    _collect_non_stream_observation,
    _collect_stream_observation,
    _CompositeMatrixExecutor,
    _emit_progress,
    _resolve_executor,
    run_tool_calling_matrix_suite,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from polaris.bootstrap.config import Settings

__all__ = [
    "ToolCallingMatrixCase",
    "load_builtin_tool_calling_matrix_cases",
    "load_tool_calling_matrix_case",
    "run_tool_calling_matrix_suite",
]
