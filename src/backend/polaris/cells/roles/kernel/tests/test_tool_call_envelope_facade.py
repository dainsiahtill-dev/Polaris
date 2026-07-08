"""Tests for the role-kernel tool-call envelope facade."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.cells.roles.kernel.internal import tool_call_envelope
from polaris.cells.roles.kernel.internal.llm_caller import tool_helpers
from polaris.cells.roles.kernel.internal.turn_engine.utils import normalize_stream_tool_call_payload

_ALIAS_ATTRS = frozenset({"native_tool_calls", "tool_calls"})


@dataclass
class _Response:
    native_tool_calls: list[dict[str, Any]]


def test_tool_helpers_reexport_canonical_facade_functions() -> None:
    assert tool_helpers.native_tool_calls_from_response is tool_call_envelope.native_tool_calls_from_response
    assert (
        tool_helpers.native_tool_call_envelopes_from_response
        is tool_call_envelope.native_tool_call_envelopes_from_response
    )
    assert tool_helpers.native_tool_call_name is tool_call_envelope.native_tool_call_name


def test_facade_prefers_metadata_envelopes_over_raw_response_calls() -> None:
    response = _Response(
        native_tool_calls=[
            {
                "id": "call-raw",
                "type": "function",
                "function": {"name": "write_file", "arguments": {"file": "a.py"}},
            }
        ]
    )
    metadata = {
        "native_tool_call_envelopes": [
            {
                "envelope_id": "env-metadata",
                "tool_name": "read_file",
                "call_id": "call-metadata",
            }
        ]
    }

    assert tool_call_envelope.native_tool_call_envelopes_from_response(response, metadata) == [
        {
            "envelope_id": "env-metadata",
            "tool_name": "read_file",
            "call_id": "call-metadata",
        }
    ]


def test_facade_wraps_raw_response_calls_when_metadata_has_no_envelopes() -> None:
    response = _Response(
        native_tool_calls=[
            {
                "id": "call-raw",
                "type": "function",
                "function": {"name": "write_file", "arguments": {"file": "a.py"}},
            }
        ]
    )

    envelopes = tool_call_envelope.native_tool_call_envelopes_from_response(
        response,
        {"tool_call_provider": "openai"},
    )

    assert len(envelopes) == 1
    assert envelopes[0]["tool_name"] == "write_file"
    assert envelopes[0]["call_id"] == "call-raw"
    assert envelopes[0]["provider"] == "openai"


def test_turn_engine_stream_wrapper_uses_facade_contract() -> None:
    payload, provider = normalize_stream_tool_call_payload(
        tool_name="write_file",
        tool_args={"file": "a.py"},
        call_id="call-stream",
        metadata={},
    )

    assert provider == "openai"
    assert payload == {
        "id": "call-stream",
        "type": "function",
        "function": {
            "name": "write_file",
            "arguments": {"file": "a.py"},
        },
    }


_KERNEL_INTERNAL = Path(__file__).resolve().parents[1] / "internal"
_FACADE_MODULE = "tool_call_envelope.py"
_TESTING_DIR = "testing"


class _GetattrAliasVisitor(ast.NodeVisitor):
    """Collect response-level native tool-call alias parsing outside the facade."""

    def __init__(self, filename: str) -> None:
        self._filename = filename
        self.violations: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value in _ALIAS_ATTRS
        ):
            self.violations.append(
                f"{self._filename}:{node.lineno}: "
                f"getattr(..., {node.args[1].value!r}) re-implements facade alias parsing"
            )
        self.generic_visit(node)


def _production_internal_python_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for py_file in root.rglob("*.py"):
        parts = py_file.relative_to(root).parts
        if _TESTING_DIR in parts or py_file.name == _FACADE_MODULE:
            continue
        result.append(py_file)
    return sorted(result)


def test_response_alias_parsing_stays_in_tool_call_envelope_facade() -> None:
    """Prevent local response-object alias tables from escaping the facade.

    ``tool_call_envelope.native_tool_calls_from_response()`` is the role
    kernel owner for the response-object ``native_tool_calls`` -> ``tool_calls``
    fallback. Raw API payload parsing in ``tool_helpers.extract_native_tool_calls``
    is a different boundary because it works on nested mapping payloads rather
    than response-like objects.
    """

    assert _KERNEL_INTERNAL.is_dir(), f"missing kernel internal directory: {_KERNEL_INTERNAL}"

    violations: list[str] = []
    for py_file in _production_internal_python_files(_KERNEL_INTERNAL):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        visitor = _GetattrAliasVisitor(filename=str(py_file.relative_to(_KERNEL_INTERNAL)))
        visitor.visit(tree)
        violations.extend(visitor.violations)

    assert violations == [], (
        "Response alias parsing found outside the tool_call_envelope facade. "
        "Use native_tool_calls_from_response() instead:\n" + "\n".join(f"  - {item}" for item in violations)
    )
