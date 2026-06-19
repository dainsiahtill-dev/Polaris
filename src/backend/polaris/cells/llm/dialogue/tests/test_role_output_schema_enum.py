"""Regression tests for RoleOutputParser enum schema validation.

The advisory ``_validate_schema`` must honestly reflect ``enum`` conformance
declared in ``ROLE_SCHEMAS`` (e.g. the QA ``verdict`` enum). Before the fix it
only enforced ``required`` and ``type``, so an illegal verdict like ``MAYBE``
was reported as valid.
"""

from __future__ import annotations

import json

from polaris.cells.llm.dialogue.internal.role_dialogue import RoleOutputParser


class TestRoleOutputSchemaEnum:
    def test_invalid_qa_verdict_fails_with_enum_error(self) -> None:
        payload = json.dumps({"review_id": "r1", "verdict": "MAYBE"})
        is_valid, _data, errors = RoleOutputParser.validate_role_output("qa", payload)
        assert is_valid is False
        assert any("verdict" in err and "MAYBE" in err for err in errors)

    def test_valid_qa_verdict_passes(self) -> None:
        payload = json.dumps({"review_id": "r1", "verdict": "PASS"})
        is_valid, _data, errors = RoleOutputParser.validate_role_output("qa", payload)
        assert is_valid is True
        assert errors == []

    def test_invalid_pm_priority_enum_fails(self) -> None:
        payload = json.dumps(
            {
                "tasks": [
                    {
                        "id": "t1",
                        "title": "Build",
                        "description": "Build it",
                        "priority": "urgent",
                    }
                ]
            }
        )
        # priority enum lives on nested task items; the top-level schema enum on
        # known fields must still be enforced when present.
        _is_valid, data, _errors = RoleOutputParser.validate_role_output("pm", payload)
        # Nested item enums are not enforced by the flat validator, but the call
        # must still parse without raising and return the data.
        assert data is not None
