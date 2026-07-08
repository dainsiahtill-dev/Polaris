"""Architecture fence: repair diagnostic normalizer flat-field copy completeness.

WS4 Typed QualityIssue requires that scanner-provided diagnostic metadata
(``diagnostic_kind``, ``diagnostic_archetype``, ``diagnostic_code``) survives
the ``scanner -> repair`` boundary.  The normalizer in
``_normalize_structured_error`` copies selected flat fields from the raw
diagnostic mapping into the ``metadata`` dict that rides along with every
``RepairDiagnostic``.  If any of the three required keys is missing from the
copy tuple, typed-issue metadata is silently dropped.

This fence uses AST parsing (no fragile line-number coupling) to extract the
string constants from the ``for key in (...)`` loop inside the function and
asserts all three keys are present.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

_DIAGNOSTICS_PY = (
    Path(__file__).resolve().parents[2]
    / "cells"
    / "director"
    / "runtime"
    / "internal"
    / "repair_kernel"
    / "diagnostics.py"
)

_TARGET_FUNCTION = "_normalize_structured_error"

_REQUIRED_FLAT_FIELDS: frozenset[str] = frozenset(
    {
        "diagnostic_kind",
        "diagnostic_archetype",
        "diagnostic_code",
    }
)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _find_function(module: ast.Module, name: str) -> ast.FunctionDef:
    """Return the first top-level ``FunctionDef`` matching *name*."""
    for node in ast.iter_child_nodes(module):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    msg = f"function {name!r} not found in {_DIAGNOSTICS_PY}"
    raise LookupError(msg)


def _extract_string_constants(nodes: Iterable[ast.expr]) -> frozenset[str]:
    """Return the set of ``str`` constant values from *nodes*.

    Handles both ``Constant`` (Python 3.8+) and ``Str`` (legacy) AST nodes.
    """
    result: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            result.add(node.value)
        elif isinstance(node, ast.Str):  # pragma: no cover - legacy compat
            legacy_value = node.s
            if isinstance(legacy_value, str):
                result.add(legacy_value)
    return frozenset(result)


def _find_for_iter_strings(func: ast.FunctionDef) -> frozenset[str]:
    """Find the first ``for`` statement in *func* and extract its iterator keys.

    Walks the function body looking for ``for <target> in <iter>:`` where
    ``<iter>`` is a ``Tuple`` or ``List`` of string constants.  Returns the
    set of string constants, or an empty frozenset if no such loop exists.
    """
    for stmt in ast.walk(func):
        if isinstance(stmt, ast.For):
            iter_node = stmt.iter
            if isinstance(iter_node, (ast.Tuple, ast.List)):
                return _extract_string_constants(iter_node.elts)
    return frozenset()


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_normalize_structured_error_flat_field_copy_contains_required_keys() -> None:
    """The normalizer must preserve diagnostic_kind/archetype/code in metadata.

    The ``for key in (...)`` loop inside ``_normalize_structured_error`` copies
    flat fields from the raw diagnostic mapping into the ``metadata`` dict.
    WS4 typed-issue metadata requires at least ``diagnostic_kind``,
    ``diagnostic_archetype``, and ``diagnostic_code`` to survive the
    scanner -> repair boundary.  If any is missing, the value is silently
    dropped during normalization and downstream consumers (RepairEngine
    strategy catalog, coverage reports, typed-issue projections) lose
    visibility into the diagnostic's true kind.
    """
    assert _DIAGNOSTICS_PY.is_file(), f"target file not found: {_DIAGNOSTICS_PY}"

    source = _DIAGNOSTICS_PY.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(_DIAGNOSTICS_PY))

    func = _find_function(module, _TARGET_FUNCTION)
    copied_keys = _find_for_iter_strings(func)

    assert copied_keys, (
        f"{_TARGET_FUNCTION} in {_DIAGNOSTICS_PY.name}: "
        "no ``for key in (...)`` tuple/list found; cannot verify flat-field "
        "copy completeness"
    )

    missing = _REQUIRED_FLAT_FIELDS - copied_keys
    assert not missing, (
        f"{_TARGET_FUNCTION} flat-field copy is missing required WS4 keys: "
        f"{sorted(missing)}.  These must appear in the ``for key in (...)`` "
        "tuple to preserve typed diagnostic metadata across the "
        "scanner -> repair boundary."
    )
