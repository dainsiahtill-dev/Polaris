"""Tests for the canonical role-output debt markers constant.

Guards the DC-5 deduplication: both consumer cells must reference the single
shared ``DEBT_MARKERS`` constant, and its frozen content must stay identical to
the historical literal so role-output quality scoring behavior is unchanged.
"""

from __future__ import annotations

from polaris.cells.llm.dialogue.internal import role_dialogue
from polaris.cells.roles.kernel.internal.quality_checker import QualityChecker
from polaris.kernelone.quality import DEBT_MARKERS as DEBT_MARKERS_FROM_PACKAGE
from polaris.kernelone.quality.role_output_markers import DEBT_MARKERS


def test_debt_markers_frozen_content() -> None:
    # Arrange
    expected = ("临时方案", "hack", "TODO", "FIXME")

    # Act
    actual = DEBT_MARKERS

    # Assert
    assert actual == expected


def test_debt_markers_is_immutable_tuple() -> None:
    # Arrange / Act / Assert
    assert isinstance(DEBT_MARKERS, tuple)


def test_package_reexport_is_same_object() -> None:
    # Arrange / Act / Assert
    assert DEBT_MARKERS_FROM_PACKAGE is DEBT_MARKERS


def test_quality_checker_uses_canonical_constant() -> None:
    # Arrange / Act / Assert
    assert QualityChecker.DEBT_MARKERS is DEBT_MARKERS


def test_role_dialogue_uses_canonical_constant() -> None:
    # Arrange / Act / Assert
    assert role_dialogue.DEBT_MARKERS is DEBT_MARKERS
