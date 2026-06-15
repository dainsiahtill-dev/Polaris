from __future__ import annotations

from datetime import datetime, timezone

from polaris.cells.llm.evaluation.public.service import readiness_freshness_issue


def test_readiness_timestamp_age_does_not_expire_successful_binding() -> None:
    assert (
        readiness_freshness_issue(
            "2000-01-01T00:00:00+00:00",
            now=datetime(2026, 6, 15, tzinfo=timezone.utc),
            max_age_seconds=1,
        )
        == ""
    )


def test_readiness_timestamp_invalid_still_reports_audit_issue() -> None:
    assert readiness_freshness_issue("not-a-timestamp") == "timestamp_invalid"
