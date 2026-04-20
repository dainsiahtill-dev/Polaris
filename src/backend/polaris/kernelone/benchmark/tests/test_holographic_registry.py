"""Tests for holographic benchmark case registry."""

from __future__ import annotations

from polaris.kernelone.benchmark.holographic_registry import (
    HOLOGRAPHIC_CASES,
    case_ids,
)


def test_registry_contains_full_49_cases() -> None:
    assert len(HOLOGRAPHIC_CASES) == 49
    assert len(set(case_ids())) == 49


def test_registry_covers_14_subsystems() -> None:
    subsystems = {case.subsystem for case in HOLOGRAPHIC_CASES}
    assert len(subsystems) == 14
    expected = {
        "S1-PHX",
        "S2-NS",
        "S3-CHR",
        "S4-TC",
        "S5-NW",
        "S6-ER",
        "S7-CM",
        "S8-AU",
        "S9-AG",
        "S10-SS",
        "S11-KS",
        "S12-ML",
        "S13-QM",
        "S14-COG",
    }
    assert subsystems == expected


def test_all_cases_are_ready() -> None:
    pending_cases = [case for case in HOLOGRAPHIC_CASES if not case.is_ready]
    assert not pending_cases
