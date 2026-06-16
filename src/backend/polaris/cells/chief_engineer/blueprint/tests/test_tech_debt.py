"""Tests for the Tech-Debt Ledger storage and helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from polaris.cells.chief_engineer.blueprint.internal.tech_debt import (
    TechDebtLedger,
    build_tech_debt_event,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ListTechDebtQueryV1,
    RegisterTechDebtCommandV1,
    TechDebtSeverity,
    TechDebtStatus,
    UpdateTechDebtStatusCommandV1,
)


class TestTechDebtLedger(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_register_persists_with_history(self) -> None:
        ledger = TechDebtLedger(self.workspace)
        record = ledger.register(
            RegisterTechDebtCommandV1(
                title="Manual SQL escaping in user_repository.py",
                description="Bypasses ORM escaping for IN clauses.",
                severity=TechDebtSeverity.SEVERE,
                surface="polaris/orm/user_repository.py",
                owner="chief_engineer",
                workspace=self.workspace,
                evidence=("git://abc123#diff-2", "ticket://DEBT-12"),
            )
        )
        self.assertTrue(record.debt_id.startswith("debt_"))
        self.assertEqual(record.status, TechDebtStatus.REGISTERED)
        self.assertEqual(len(record.history), 1)
        path = Path(self.workspace) / "runtime" / "tech_debt" / f"{record.debt_id}.json"
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["severity"], "severe")
        self.assertEqual(data["evidence"], ["git://abc123#diff-2", "ticket://DEBT-12"])

    def test_list_filters_by_surface_and_status(self) -> None:
        ledger = TechDebtLedger(self.workspace)
        a = ledger.register(
            RegisterTechDebtCommandV1(
                title="t1",
                description="",
                severity=TechDebtSeverity.MAJOR,
                surface="dir/a",
                owner="chief_engineer",
                workspace=self.workspace,
            )
        )
        b = ledger.register(
            RegisterTechDebtCommandV1(
                title="t2",
                description="",
                severity=TechDebtSeverity.MINOR,
                surface="dir/b",
                owner="chief_engineer",
                workspace=self.workspace,
            )
        )
        listed_a = ledger.list(surface="dir/a")
        self.assertEqual([r.debt_id for r in listed_a], [a.debt_id])
        listed_minor = ledger.list(severity=TechDebtSeverity.MINOR)
        self.assertEqual([r.debt_id for r in listed_minor], [b.debt_id])

    def test_update_status_appends_history(self) -> None:
        ledger = TechDebtLedger(self.workspace)
        record = ledger.register(
            RegisterTechDebtCommandV1(
                title="t",
                description="d",
                severity=TechDebtSeverity.MINOR,
                surface="s",
                owner="chief_engineer",
                workspace=self.workspace,
            )
        )
        updated = ledger.update_status(
            UpdateTechDebtStatusCommandV1(
                workspace=self.workspace,
                debt_id=record.debt_id,
                status=TechDebtStatus.SCHEDULED,
                note="planned for sprint 14",
            ),
            actor="chief_engineer",
        )
        self.assertEqual(updated.status, TechDebtStatus.SCHEDULED)
        self.assertEqual(len(updated.history), 2)
        self.assertEqual(updated.history[1]["note"], "planned for sprint 14")

    def test_list_for_query_respects_all_filters(self) -> None:
        ledger = TechDebtLedger(self.workspace)
        record = ledger.register(
            RegisterTechDebtCommandV1(
                title="t",
                description="d",
                severity=TechDebtSeverity.MAJOR,
                surface="dir/x",
                owner="chief_engineer",
                workspace=self.workspace,
            )
        )
        out = ledger.list_for_query(
            ListTechDebtQueryV1(
                workspace=self.workspace,
                severity=TechDebtSeverity.MAJOR,
                surface="dir/x",
                status=TechDebtStatus.REGISTERED,
            )
        )
        self.assertEqual([r.debt_id for r in out], [record.debt_id])

    def test_summarize_counts(self) -> None:
        ledger = TechDebtLedger(self.workspace)
        ledger.register(
            RegisterTechDebtCommandV1(
                title="t1",
                description="",
                severity=TechDebtSeverity.MAJOR,
                surface="s",
                owner="chief_engineer",
                workspace=self.workspace,
            )
        )
        ledger.register(
            RegisterTechDebtCommandV1(
                title="t2",
                description="",
                severity=TechDebtSeverity.MINOR,
                surface="s",
                owner="chief_engineer",
                workspace=self.workspace,
            )
        )
        summary = ledger.summarize(surface="s")
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["by_severity"]["major"], 1)
        self.assertEqual(summary["by_severity"]["minor"], 1)
        self.assertEqual(summary["by_status"]["registered"], 2)

    def test_build_tech_debt_event_stamps(self) -> None:
        event = build_tech_debt_event(
            debt_id="debt_x",
            workspace=self.workspace,
            action="status:paid",
            actor="chief_engineer",
            note="removed",
        )
        self.assertTrue(event.event_id.startswith("debtevt_"))
        self.assertEqual(event.action, "status:paid")


if __name__ == "__main__":
    unittest.main()
