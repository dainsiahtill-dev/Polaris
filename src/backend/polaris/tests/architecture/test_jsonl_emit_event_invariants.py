"""Architecture Invariant Tests: JSONL emit_event and _append_jsonl dedup.

Guards against regression of duplicate JSONL write implementations and
emit_event payload construction outside the canonical kernelone.events path.

Rule: JSONL writes MUST delegate to kernelone.events.io_events or
kernelone.fs.jsonl.ops.  Cells / infrastructure must NOT reimplement
append-jsonl or emit_event payload assembly inline.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"

# Canonical JSONL write entry-points (allowed to contain raw file I/O)
CANONICAL_JSONL_MODULES: set[str] = {
    "polaris.kernelone.events.io_events",
    "polaris.kernelone.fs.jsonl.ops",
    "polaris.kernelone.process.background_manager",
    "polaris.kernelone.audit.runtime",
    "polaris.infrastructure.compat.io_utils",
    "polaris.infrastructure.log_pipeline.writer",
    "polaris.infrastructure.log_pipeline.adapters",
    "polaris.infrastructure.accel.verify.verify.report_generator",
}

# Directories that must NOT contain standalone _append_jsonl definitions
NON_CANONICAL_ROOTS: list[Path] = [
    POLARIS_ROOT / "cells",
    POLARIS_ROOT / "delivery",
    POLARIS_ROOT / "application",
    POLARIS_ROOT / "domain",
]

# Canonical emit_event with full JSONL write logic
CANONICAL_EMIT_EVENT_MODULE = "polaris.kernelone.events.io_events"


def _module_name(path: Path) -> str:
    rel = path.relative_to(BACKEND_ROOT).with_suffix("")
    return ".".join(rel.parts)


def _is_test_file(path: Path) -> bool:
    parts = path.parts
    return "test" in path.name or "tests" in parts or "__pycache__" in str(path)


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if not _is_test_file(p) and "__pycache__" not in str(p))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ─── Test 1: No duplicate _append_jsonl in cells/ ────────────────────────────


def test_no_duplicate_append_jsonl_in_cells() -> None:
    """Cells must NOT define their own _append_jsonl; they must delegate to kernelone."""
    violations: list[str] = []

    for root in NON_CANONICAL_ROOTS:
        if not root.exists():
            continue
        for path in _iter_python_files(root):
            mod = _module_name(path)
            if mod in CANONICAL_JSONL_MODULES:
                continue
            try:
                content = _read_text(path)
            except (UnicodeDecodeError, OSError):
                continue

            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_append_jsonl":
                    # Check if body contains raw file I/O (not delegation)
                    body_src = ast.get_source_segment(content, node) or ""
                    has_raw_io = any(kw in body_src for kw in ["open(", ".write(", "json.dumps", "append_text_atomic"])
                    if has_raw_io:
                        violations.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno}")

    assert violations == [], (
        f"Found {len(violations)} non-canonical _append_jsonl with raw I/O:\n"
        + "\n".join(f"  - {v}" for v in violations[:15])
        + "\nDelegate to kernelone.events.io_events or kernelone.fs.jsonl.ops."
    )


# ─── Test 2: No duplicate emit_event with full payload construction ──────────


def test_no_duplicate_emit_event_with_payload_construction() -> None:
    """emit_event with full JSONL payload assembly must only exist in kernelone.events.io_events.

    Other modules may have emit_event as a thin wrapper / adapter, but must NOT
    reconstruct schema_version/ts/seq/event_id/payload locally.
    """
    violations: list[str] = []
    payload_indicators = ["schema_version", "event_id", "ts_epoch", '"seq"']

    for path in _iter_python_files(POLARIS_ROOT):
        mod = _module_name(path)
        if mod == CANONICAL_EMIT_EVENT_MODULE:
            continue
        if mod in CANONICAL_JSONL_MODULES:
            continue
        try:
            content = _read_text(path)
        except (UnicodeDecodeError, OSError):
            continue

        tree = ast.parse(content)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != "emit_event":
                continue
            body_src = ast.get_source_segment(content, node) or ""
            hits = [ind for ind in payload_indicators if ind in body_src]
            if len(hits) >= 2:
                violations.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} (indicators: {hits})")

    assert violations == [], (
        f"Found {len(violations)} emit_event with duplicate payload construction:\n"
        + "\n".join(f"  - {v}" for v in violations[:15])
        + "\nUse kernelone.events.io_events.emit_event instead."
    )


# ─── Test 3: Dead adapt_emit_event detection ─────────────────────────────────


def test_adapt_emit_event_has_callers_or_is_in_compat() -> None:
    """adapt_emit_event must have at least one caller or be in a compat/deprecated module.

    Dead code with duplicated payload logic is a maintenance risk.
    """
    adapters_path = POLARIS_ROOT / "infrastructure" / "log_pipeline" / "adapters.py"
    if not adapters_path.exists():
        pytest.skip("adapters.py not found")

    content = _read_text(adapters_path)
    tree = ast.parse(content)

    has_adapt = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "adapt_emit_event"
        for node in ast.walk(tree)
    )
    if not has_adapt:
        return  # function doesn't exist, nothing to guard

    # Search for callers across the codebase
    callers: list[str] = []
    for path in _iter_python_files(POLARIS_ROOT):
        if path == adapters_path:
            continue
        try:
            text = _read_text(path)
        except (UnicodeDecodeError, OSError):
            continue
        if "adapt_emit_event" in text:
            callers.append(str(path.relative_to(BACKEND_ROOT)))

    # Also check test files
    for path in sorted(POLARIS_ROOT.rglob("test_*.py")):
        try:
            text = _read_text(path)
        except (UnicodeDecodeError, OSError):
            continue
        if "adapt_emit_event" in text:
            callers.append(str(path.relative_to(BACKEND_ROOT)))

    assert len(callers) > 0, (
        "adapt_emit_event in infrastructure/log_pipeline/adapters.py has ZERO callers "
        "and contains duplicated payload construction logic. "
        "Remove it or wire it up."
    )


# ─── Test 4: cells/ _append_jsonl must delegate, not do raw file I/O ─────────


def test_cells_append_jsonl_delegates_to_kernel() -> None:
    """Any _append_jsonl in cells/ must be a thin delegation wrapper, not raw file I/O."""
    cells_dir = POLARIS_ROOT / "cells"
    if not cells_dir.exists():
        pytest.skip("cells/ not found")

    violations: list[str] = []
    raw_io_patterns = ["open(", ".write(", "json.dumps", "append_text_atomic", "path.open"]

    for path in _iter_python_files(cells_dir):
        try:
            content = _read_text(path)
        except (UnicodeDecodeError, OSError):
            continue

        tree = ast.parse(content)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != "_append_jsonl":
                continue
            body_src = ast.get_source_segment(content, node) or ""
            hits = [p for p in raw_io_patterns if p in body_src]
            if hits:
                violations.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno} (patterns: {hits})")

    assert violations == [], (
        f"Found {len(violations)} cells/ _append_jsonl with raw file I/O:\n"
        + "\n".join(f"  - {v}" for v in violations[:15])
        + "\nDelegate to kernelone JSONL ops."
    )


# ─── Test 5: Canonical emit_event signature stability ────────────────────────


def test_canonical_emit_event_signature_stable() -> None:
    """kernelone.events.io_events.emit_event must have the expected keyword-only params."""
    import inspect

    from polaris.kernelone.events.io_events import emit_event

    sig = inspect.signature(emit_event)
    params = list(sig.parameters.keys())

    required = ["event_path", "kind", "actor", "name"]
    for p in required:
        assert p in params, f"emit_event missing required param: {p}"

    # Verify keyword-only after event_path
    kw_only = [p for p, v in sig.parameters.items() if v.kind == inspect.Parameter.KEYWORD_ONLY]
    assert "kind" in kw_only, "kind must be keyword-only"
    assert "actor" in kw_only, "actor must be keyword-only"


# ─── Test 6: io_utils.emit_event delegates (not duplicates) ──────────────────


def test_io_utils_emit_event_delegates_to_io_events() -> None:
    """infrastructure/compat/io_utils.emit_event must delegate to kernelone.events.io_events."""
    io_utils_path = POLARIS_ROOT / "infrastructure" / "compat" / "io_utils.py"
    if not io_utils_path.exists():
        pytest.skip("io_utils.py not found")

    content = _read_text(io_utils_path)
    tree = ast.parse(content)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "emit_event":
            continue
        body_src = ast.get_source_segment(content, node) or ""
        # Must delegate to io_events, not reimplement
        assert "io_events.emit_event" in body_src or "io_events" in body_src, (
            "io_utils.emit_event must delegate to io_events.emit_event, not reimplement payload construction."
        )
        # Must NOT contain payload construction indicators
        duplicate_indicators = ["schema_version", "event_id", "ts_epoch"]
        hits = [ind for ind in duplicate_indicators if ind in body_src]
        assert hits == [], (
            f"io_utils.emit_event contains payload construction indicators {hits}; "
            "it should be a thin delegation wrapper."
        )
