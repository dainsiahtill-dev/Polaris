from tests.agent_stress.engine import StressEngine


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
