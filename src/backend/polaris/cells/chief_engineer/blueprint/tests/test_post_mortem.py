"""Tests for the Post-Mortem / Incident Review log."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from polaris.cells.chief_engineer.blueprint.internal.post_mortem import (
    PostMortemLog,
    build_post_mortem_event,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    IncidentSeverity,
    ListPostMortemsQueryV1,
    PostMortemStatus,
    RegisterPostMortemCommandV1,
    UpdatePostMortemStatusCommandV1,
)
from polaris.cells.chief_engineer.blueprint.public.service import (
    list_post_mortems,
    register_post_mortem,
    summarize_post_mortems,
    update_post_mortem_status,
)
from polaris.kernelone.storage import resolve_logical_path


class TestPostMortemLog(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_register_persists_full_record(self) -> None:
        log = PostMortemLog(self.workspace)
        record = log.register(
            RegisterPostMortemCommandV1(
                title="prod outage: write amplification",
                severity=IncidentSeverity.SEV1,
                occurred_at="2026-06-16T10:00:00Z",
                owner="chief_engineer",
                workspace=self.workspace,
                summary="DB saturated under retry storm",
                root_cause="unbounded retry without jitter",
                impact="42 min partial downtime",
                timeline=("10:00 alert", "10:12 mitigated"),
                action_items=("add jitter", "cap retries"),
                related_risk_ids=("risk_x",),
            )
        )
        self.assertTrue(record.incident_id.startswith("incident_"))
        self.assertEqual(record.severity, IncidentSeverity.SEV1)
        self.assertEqual(record.status, PostMortemStatus.DRAFT)
        self.assertEqual(record.action_items, ("add jitter", "cap retries"))
        path = Path(resolve_logical_path(self.workspace, "runtime/post_mortems")) / f"{record.incident_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["severity"], "sev1")
        self.assertEqual(data["timeline"], ["10:00 alert", "10:12 mitigated"])

    def test_update_status_appends_history(self) -> None:
        log = PostMortemLog(self.workspace)
        record = log.register(
            RegisterPostMortemCommandV1(
                title="t",
                severity=IncidentSeverity.SEV3,
                occurred_at="2026-06-16T10:00:00Z",
                owner="ce",
                workspace=self.workspace,
            )
        )
        updated = log.update_status(
            UpdatePostMortemStatusCommandV1(
                workspace=self.workspace,
                incident_id=record.incident_id,
                status=PostMortemStatus.PUBLISHED,
                note="reviewed in eng sync",
            ),
            actor="ce",
        )
        self.assertEqual(updated.status, PostMortemStatus.PUBLISHED)
        self.assertEqual(updated.history[-1]["note"], "reviewed in eng sync")

    def test_list_filters_by_severity_and_status(self) -> None:
        log = PostMortemLog(self.workspace)
        a = log.register(
            RegisterPostMortemCommandV1(
                title="a",
                severity=IncidentSeverity.SEV1,
                occurred_at="t",
                owner="ce",
                workspace=self.workspace,
            )
        )
        log.register(
            RegisterPostMortemCommandV1(
                title="b",
                severity=IncidentSeverity.SEV4,
                occurred_at="t",
                owner="ce",
                workspace=self.workspace,
            )
        )
        sev1 = log.list(severity=IncidentSeverity.SEV1)
        self.assertEqual([r.incident_id for r in sev1], [a.incident_id])

    def test_summarize_counts_open_action_items(self) -> None:
        log = PostMortemLog(self.workspace)
        log.register(
            RegisterPostMortemCommandV1(
                title="open",
                severity=IncidentSeverity.SEV2,
                occurred_at="t",
                owner="ce",
                workspace=self.workspace,
                action_items=("a1", "a2"),
            )
        )
        closed = log.register(
            RegisterPostMortemCommandV1(
                title="closed",
                severity=IncidentSeverity.SEV3,
                occurred_at="t",
                owner="ce",
                workspace=self.workspace,
                action_items=("a3",),
            )
        )
        log.update_status(
            UpdatePostMortemStatusCommandV1(
                workspace=self.workspace,
                incident_id=closed.incident_id,
                status=PostMortemStatus.CLOSED,
            ),
            actor="ce",
        )
        summary = log.summarize()
        self.assertEqual(summary["total"], 2)
        # Only the open post-mortem's 2 action items count.
        self.assertEqual(summary["open_action_items"], 2)

    def test_path_traversal_rejected(self) -> None:
        log = PostMortemLog(self.workspace)
        for evil in ("../../etc/passwd", "a/b", ".."):
            with self.assertRaises(ValueError):
                log.load(evil)
        with self.assertRaises(ValueError):
            log.update_status(
                UpdatePostMortemStatusCommandV1(
                    workspace=self.workspace,
                    incident_id="../../x",
                    status=PostMortemStatus.CLOSED,
                ),
                actor="ce",
            )

    def test_load_tolerates_invalid_enum_on_disk(self) -> None:
        log = PostMortemLog(self.workspace)
        record = log.register(
            RegisterPostMortemCommandV1(
                title="t",
                severity=IncidentSeverity.SEV1,
                occurred_at="t",
                owner="ce",
                workspace=self.workspace,
            )
        )
        path = Path(resolve_logical_path(self.workspace, "runtime/post_mortems")) / f"{record.incident_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["severity"] = "sev99"
        data["status"] = "bogus"
        path.write_text(json.dumps(data), encoding="utf-8")
        listed = log.list()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].severity, IncidentSeverity.SEV3)
        self.assertEqual(listed[0].status, PostMortemStatus.DRAFT)

    def test_build_event(self) -> None:
        event = build_post_mortem_event(
            incident_id="incident_x", workspace=self.workspace, action="recorded", actor="ce"
        )
        self.assertTrue(event.event_id.startswith("pmevt_"))


class TestPostMortemServiceSurface(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_service_round_trip(self) -> None:
        record = register_post_mortem(
            RegisterPostMortemCommandV1(
                title="cache stampede",
                severity=IncidentSeverity.SEV2,
                occurred_at="2026-06-16T10:00:00Z",
                owner="chief_engineer",
                workspace=self.workspace,
            )
        )
        listed = list_post_mortems(
            ListPostMortemsQueryV1(workspace=self.workspace, severity=IncidentSeverity.SEV2)
        )
        self.assertEqual([r.incident_id for r in listed], [record.incident_id])
        update_post_mortem_status(
            UpdatePostMortemStatusCommandV1(
                workspace=self.workspace,
                incident_id=record.incident_id,
                status=PostMortemStatus.CLOSED,
            )
        )
        summary = summarize_post_mortems(self.workspace)
        self.assertEqual(summary["by_status"]["closed"], 1)


if __name__ == "__main__":
    unittest.main()
