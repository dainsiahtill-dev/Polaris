"""Tests for agent stress post-batch audit rules."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Try normal import first; fall back to dynamic load if package resolution fails
try:
    from polaris.tests.agent_stress.engine import StressEngine
except ImportError:
    # Load StressEngine directly to avoid package import issues
    _engine_path = Path(__file__).parent / "agent_stress" / "engine.py"
    _spec = importlib.util.spec_from_file_location("agent_stress_engine", _engine_path)
    if _spec is None or _spec.loader is None:
        pytest.skip(f"Cannot load StressEngine from {_engine_path}", allow_module_level=True)
    _agent_stress_engine = importlib.util.module_from_spec(_spec)
    sys.modules["agent_stress_engine"] = _agent_stress_engine
    # Set up package context for relative imports in engine.py
    sys.modules["agent_stress"] = type(sys)("agent_stress")
    sys.modules["agent_stress"].__path__ = [str(_engine_path.parent)]
    _spec.loader.exec_module(_agent_stress_engine)
    StressEngine = _agent_stress_engine.StressEngine


def test_empty_function_rule_ignores_non_empty_python_function_with_docstring() -> None:
    content = """
def tracked_execution(tracker, task_id,
                      metadata=None):
    \"\"\"Track execution lifecycle.\"\"\"
    payload = metadata or {}
    payload["task_id"] = task_id
    return payload
""".strip()

    matches = StressEngine._extract_empty_function_matches(content, ".py")

    assert matches == []


def test_empty_function_rule_handles_non_docstring_first_statement() -> None:
    content = """
def invoke_side_effect(callback):
    callback()
    return True
""".strip()

    matches = StressEngine._extract_empty_function_matches(content, ".py")

    assert matches == []


def test_empty_function_rule_detects_python_stub_variants() -> None:
    content = """
def pending_pass():
    pass

def pending_ellipsis():
    ...

def pending_docstring():
    \"\"\"TODO\"\"\"
""".strip()

    matches = StressEngine._extract_empty_function_matches(content, ".py")

    assert "pending_pass(pass)" in matches
    assert "pending_ellipsis(ellipsis)" in matches
    assert "pending_docstring(docstring_only)" in matches


def test_empty_function_rule_detects_js_ts_empty_blocks() -> None:
    content = """
function noop() {}
const emptyArrow = () => {}
""".strip()

    matches = StressEngine._extract_empty_function_matches(content, ".ts")

    assert len(matches) == 2


def test_empty_function_rule_ignores_protocol_methods() -> None:
    content = """
from typing import Protocol

class ExpenseRepository(Protocol):
    def save(self, payload) -> None:
        ...

def concrete_stub():
    pass
""".strip()

    matches = StressEngine._extract_empty_function_matches(content, ".py")

    assert "save(ellipsis)" not in matches
    assert "concrete_stub(pass)" in matches
