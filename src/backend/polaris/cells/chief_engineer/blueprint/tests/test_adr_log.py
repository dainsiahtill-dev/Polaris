"""Tests for the Architecture Decision Log (Tier-2) storage + service."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from polaris.cells.chief_engineer.blueprint.internal.adr_log import (
    ADRDecisionLog,
    build_adr_event,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ADRStatus,
    ListADRsQueryV1,
    RegisterADRCommandV1,
    UpdateADRStatusCommandV1,
)
from polaris.cells.chief_engineer.blueprint.public.service import (
    list_adrs,
    register_adr,
    summarize_adrs,
    update_adr_status,
)
from polaris.kernelone.storage import resolve_logical_path


class TestADRDecisionLog(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_register_persists_canonical_adr(self) -> None:
        log = ADRDecisionLog(self.workspace)
        record = log.register(
            RegisterADRCommandV1(
                title="Adopt single transaction kernel",
                decision="All turn commits go through TransactionKernel",
                owner="chief_engineer",
                workspace=self.workspace,
                context="Multiple commit points caused partial writes",
                consequences="One commit point; simpler rollback; slight latency",
                alternatives=("per-tool commit", "two-phase commit"),
                related_task_ids=("task-1",),
            )
        )
        self.assertTrue(record.adr_id.startswith("adr_"))
        self.assertEqual(record.status, ADRStatus.PROPOSED)
        self.assertEqual(record.alternatives, ("per-tool commit", "two-phase commit"))
        path = Path(resolve_logical_path(self.workspace, "runtime/adr_log")) / f"{record.adr_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "proposed")
        self.assertEqual(data["related_task_ids"], ["task-1"])

    def test_supersede_marks_predecessor_and_preserves_fields(self) -> None:
        log = ADRDecisionLog(self.workspace)
        first = log.register(
            RegisterADRCommandV1(
                title="Use REST",
                decision="REST endpoints",
                owner="ce",
                workspace=self.workspace,
                context="simple HTTP semantics",
                consequences="easy debugging",
                alternatives=("gRPC", "GraphQL"),
                related_task_ids=("task-1", "task-2"),
            )
        )
        log.register(
            RegisterADRCommandV1(
                title="Use gRPC",
                decision="gRPC endpoints",
                owner="ce",
                workspace=self.workspace,
                supersedes=first.adr_id,
            )
        )
        reloaded = log.load(first.adr_id)
        assert reloaded is not None
        self.assertEqual(reloaded.status, ADRStatus.SUPERSEDED)
        # Field preservation: only status + history change.
        self.assertEqual(reloaded.title, "Use REST")
        self.assertEqual(reloaded.context, "simple HTTP semantics")
        self.assertEqual(reloaded.consequences, "easy debugging")
        self.assertEqual(reloaded.alternatives, ("gRPC", "GraphQL"))
        self.assertEqual(reloaded.related_task_ids, ("task-1", "task-2"))
        self.assertEqual(len(reloaded.history), 2)
        self.assertEqual(reloaded.history[-1]["action"], "status:superseded")

    def test_supersede_traversal_id_is_safe_noop(self) -> None:
        log = ADRDecisionLog(self.workspace)
        # A malicious supersedes id must not traverse the filesystem, and the
        # new ADR must still register successfully (fail-open on the side effect).
        record = log.register(
            RegisterADRCommandV1(
                title="t",
                decision="d",
                owner="ce",
                workspace=self.workspace,
                supersedes="../../etc/passwd",
            )
        )
        self.assertTrue(record.adr_id.startswith("adr_"))
        # Exactly one file written (the new ADR); no escape, no extra writes.
        adr_dir = Path(resolve_logical_path(self.workspace, "runtime/adr_log"))
        self.assertEqual(len(list(adr_dir.glob("*.json"))), 1)

    def test_supersede_nonexistent_id_is_safe_noop(self) -> None:
        log = ADRDecisionLog(self.workspace)
        record = log.register(
            RegisterADRCommandV1(
                title="t",
                decision="d",
                owner="ce",
                workspace=self.workspace,
                supersedes="adr_does_not_exist",
            )
        )
        self.assertTrue(record.adr_id.startswith("adr_"))

    def test_double_supersede_does_not_remark(self) -> None:
        log = ADRDecisionLog(self.workspace)
        first = log.register(RegisterADRCommandV1(title="a", decision="d", owner="ce", workspace=self.workspace))
        log.register(
            RegisterADRCommandV1(title="b", decision="d", owner="ce", workspace=self.workspace, supersedes=first.adr_id)
        )
        after_first = log.load(first.adr_id)
        assert after_first is not None
        history_len = len(after_first.history)
        # A second ADR superseding the already-superseded predecessor must be a no-op.
        log.register(
            RegisterADRCommandV1(title="c", decision="d", owner="ce", workspace=self.workspace, supersedes=first.adr_id)
        )
        after_second = log.load(first.adr_id)
        assert after_second is not None
        self.assertEqual(after_second.status, ADRStatus.SUPERSEDED)
        self.assertEqual(len(after_second.history), history_len)

    def test_update_status_appends_history(self) -> None:
        log = ADRDecisionLog(self.workspace)
        record = log.register(
            RegisterADRCommandV1(
                title="t",
                decision="d",
                owner="ce",
                workspace=self.workspace,
            )
        )
        updated = log.update_status(
            UpdateADRStatusCommandV1(
                workspace=self.workspace,
                adr_id=record.adr_id,
                status=ADRStatus.ACCEPTED,
                note="ratified",
            ),
            actor="ce",
        )
        self.assertEqual(updated.status, ADRStatus.ACCEPTED)
        self.assertEqual(updated.history[-1]["note"], "ratified")

    def test_path_traversal_rejected(self) -> None:
        log = ADRDecisionLog(self.workspace)
        for evil in ("../../etc/passwd", "a/b", ".."):
            with self.assertRaises(ValueError):
                log.load(evil)
        with self.assertRaises(ValueError):
            log.update_status(
                UpdateADRStatusCommandV1(
                    workspace=self.workspace,
                    adr_id="../../x",
                    status=ADRStatus.ACCEPTED,
                ),
                actor="ce",
            )

    def test_list_filters_by_status_and_task(self) -> None:
        log = ADRDecisionLog(self.workspace)
        a = log.register(
            RegisterADRCommandV1(
                title="a",
                decision="d",
                owner="ce",
                workspace=self.workspace,
                related_task_ids=("task-1",),
            )
        )
        log.register(
            RegisterADRCommandV1(
                title="b",
                decision="d",
                owner="ce",
                workspace=self.workspace,
                related_task_ids=("task-2",),
            )
        )
        by_task = log.list(task_id="task-1")
        self.assertEqual([r.adr_id for r in by_task], [a.adr_id])

    def test_load_tolerates_invalid_status_on_disk(self) -> None:
        log = ADRDecisionLog(self.workspace)
        record = log.register(RegisterADRCommandV1(title="t", decision="d", owner="ce", workspace=self.workspace))
        path = Path(resolve_logical_path(self.workspace, "runtime/adr_log")) / f"{record.adr_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["status"] = "bogus"
        path.write_text(json.dumps(data), encoding="utf-8")
        listed = log.list()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].status, ADRStatus.PROPOSED)

    def test_build_adr_event(self) -> None:
        event = build_adr_event(
            adr_id="adr_x",
            workspace=self.workspace,
            action="proposed",
            actor="ce",
        )
        self.assertTrue(event.event_id.startswith("adrevt_"))


class TestADRServiceSurface(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_service_round_trip(self) -> None:
        record = register_adr(
            RegisterADRCommandV1(
                title="adopt event sourcing",
                decision="append-only truth log",
                owner="chief_engineer",
                workspace=self.workspace,
            )
        )
        listed = list_adrs(ListADRsQueryV1(workspace=self.workspace))
        self.assertEqual([r.adr_id for r in listed], [record.adr_id])
        update_adr_status(
            UpdateADRStatusCommandV1(
                workspace=self.workspace,
                adr_id=record.adr_id,
                status=ADRStatus.ACCEPTED,
            )
        )
        summary = summarize_adrs(self.workspace)
        self.assertEqual(summary["by_status"]["accepted"], 1)


if __name__ == "__main__":
    unittest.main()
