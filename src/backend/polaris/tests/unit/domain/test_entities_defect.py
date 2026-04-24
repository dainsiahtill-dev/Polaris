"""Tests for polaris.domain.entities.defect."""

from __future__ import annotations

from polaris.domain.entities.defect import DEFAULT_DEFECT_TICKET_FIELDS


class TestDefectConstants:
    def test_required_fields(self) -> None:
        assert "defect_id" in DEFAULT_DEFECT_TICKET_FIELDS
        assert "severity" in DEFAULT_DEFECT_TICKET_FIELDS
        assert "repro_steps" in DEFAULT_DEFECT_TICKET_FIELDS
        assert "expected" in DEFAULT_DEFECT_TICKET_FIELDS
        assert "actual" in DEFAULT_DEFECT_TICKET_FIELDS
        assert "artifact_path" in DEFAULT_DEFECT_TICKET_FIELDS
        assert "suspected_scope" in DEFAULT_DEFECT_TICKET_FIELDS
        assert len(DEFAULT_DEFECT_TICKET_FIELDS) == 7
