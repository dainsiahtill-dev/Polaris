"""Tests for dialogue role registry alignment."""

from __future__ import annotations

from polaris.cells.llm.dialogue.public import get_registered_roles


def test_registered_roles_include_resident_agi() -> None:
    roles = get_registered_roles()

    assert "resident_agi" in roles
