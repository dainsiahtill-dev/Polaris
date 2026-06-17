"""Tests for the Risk Register storage and helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from polaris.cells.chief_engineer.blueprint.internal.risks import (
    RiskRegister,
    build_risk_event,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    RegisterRiskCommandV1,
    RiskSeverity,
    RiskStatus,
    UpdateRiskStatusCommandV1,
)
from polaris.kernelone.storage import resolve_logical_path


class _WorkspaceFixture:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = self._tmp.name

    def cleanup(self) -> None:
        self._tmp.cleanup()


class TestRiskRegister(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _WorkspaceFixture()
        self.workspace = self.fx.path

    def tearDown(self) -> None:
        self.fx.cleanup()

    def test_register_creates_atomically(self) -> None:
        register = RiskRegister(self.workspace)
        cmd = RegisterRiskCommandV1(
            task_id="task-1",
            title="schema migration may break legacy clients",
            severity=RiskSeverity.HIGH,
            owner="chief_engineer",
            mitigation="stage behind feature flag; double-write to old schema for 7 days",
            workspace=self.workspace,
            links=("runbook://schema-migration", "ticket://DB-901"),
        )
        record = register.register(cmd)
        self.assertTrue(record.risk_id.startswith("risk_task-1_"))
        self.assertEqual(record.status, RiskStatus.OPEN)
        self.assertEqual(record.severity, RiskSeverity.HIGH)
        self.assertEqual(record.supersedes, None)
        self.assertEqual(len(record.history), 1)
        self.assertEqual(record.history[0]["action"], "registered")

        path = Path(resolve_logical_path(self.workspace, "runtime/risks")) / f"{record.risk_id}.json"
        self.assertTrue(path.exists())
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["severity"], "high")
        self.assertEqual(data["status"], "open")
        self.assertEqual(data["links"], ["runbook://schema-migration", "ticket://DB-901"])

    def test_list_filters_by_task_and_severity(self) -> None:
        register = RiskRegister(self.workspace)
        a = register.register(
            RegisterRiskCommandV1(
                task_id="task-1",
                title="alpha",
                severity=RiskSeverity.LOW,
                owner="chief_engineer",
                mitigation="m",
                workspace=self.workspace,
            )
        )
        b = register.register(
            RegisterRiskCommandV1(
                task_id="task-2",
                title="beta",
                severity=RiskSeverity.CRITICAL,
                owner="chief_engineer",
                mitigation="m",
                workspace=self.workspace,
            )
        )
        c = register.register(
            RegisterRiskCommandV1(
                task_id="task-1",
                title="gamma",
                severity=RiskSeverity.HIGH,
                owner="chief_engineer",
                mitigation="m",
                workspace=self.workspace,
            )
        )
        listed_task_1 = register.list(task_id="task-1")
        self.assertEqual({r.risk_id for r in listed_task_1}, {a.risk_id, c.risk_id})
        self.assertEqual(len(listed_task_1), 2)
        critical = register.list(severity=RiskSeverity.CRITICAL)
        self.assertEqual([r.risk_id for r in critical], [b.risk_id])

    def test_update_status_appends_history(self) -> None:
        register = RiskRegister(self.workspace)
        record = register.register(
            RegisterRiskCommandV1(
                task_id="task-1",
                title="t",
                severity=RiskSeverity.BLOCKER,
                owner="chief_engineer",
                mitigation="m",
                workspace=self.workspace,
            )
        )
        updated = register.update_status(
            UpdateRiskStatusCommandV1(
                workspace=self.workspace,
                risk_id=record.risk_id,
                status=RiskStatus.MITIGATING,
                note="feature flag staged",
            ),
            actor="chief_engineer",
        )
        self.assertEqual(updated.status, RiskStatus.MITIGATING)
        self.assertEqual(len(updated.history), 2)
        self.assertEqual(updated.history[1]["action"], "status:mitigating")
        self.assertEqual(updated.history[1]["note"], "feature flag staged")

    def test_update_missing_risk_raises(self) -> None:
        register = RiskRegister(self.workspace)
        with self.assertRaises(FileNotFoundError):
            register.update_status(
                UpdateRiskStatusCommandV1(
                    workspace=self.workspace,
                    risk_id="risk_does_not_exist",
                    status=RiskStatus.RESOLVED,
                ),
                actor="chief_engineer",
            )

    def test_summarize_counts_open_critical_or_blocker(self) -> None:
        register = RiskRegister(self.workspace)
        register.register(
            RegisterRiskCommandV1(
                task_id="task-1",
                title="open blocker",
                severity=RiskSeverity.BLOCKER,
                owner="chief_engineer",
                mitigation="m",
                workspace=self.workspace,
            )
        )
        register.register(
            RegisterRiskCommandV1(
                task_id="task-1",
                title="critical",
                severity=RiskSeverity.CRITICAL,
                owner="chief_engineer",
                mitigation="m",
                workspace=self.workspace,
            )
        )
        register.register(
            RegisterRiskCommandV1(
                task_id="task-1",
                title="low",
                severity=RiskSeverity.LOW,
                owner="chief_engineer",
                mitigation="m",
                workspace=self.workspace,
            )
        )
        summary = register.summarize(task_id="task-1")
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["open_critical_or_blocker"], 2)
        self.assertEqual(summary["by_severity"]["blocker"], 1)
        self.assertEqual(summary["by_severity"]["critical"], 1)
        self.assertEqual(summary["by_severity"]["low"], 1)

    def test_load_handles_corrupt_file(self) -> None:
        register = RiskRegister(self.workspace)
        bad = Path(resolve_logical_path(self.workspace, "runtime/risks")) / "risk_corrupt.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        with open(bad, "w", encoding="utf-8") as handle:
            handle.write("{not valid json")
        # load should return None
        self.assertIsNone(register.load("risk_corrupt"))

    def test_path_traversal_id_is_rejected_on_load(self) -> None:
        register = RiskRegister(self.workspace)
        for evil in ("../../etc/passwd", "..\\..\\x", "a/b", "risk/../../x", ""):
            with self.assertRaises(ValueError):
                register.load(evil)

    def test_path_traversal_id_is_rejected_on_update(self) -> None:
        register = RiskRegister(self.workspace)
        with self.assertRaises(ValueError):
            register.update_status(
                UpdateRiskStatusCommandV1(
                    workspace=self.workspace,
                    risk_id="../../../tmp/evil",
                    status=RiskStatus.RESOLVED,
                ),
                actor="chief_engineer",
            )

    def test_generated_ids_are_unique_under_tight_loop(self) -> None:
        register = RiskRegister(self.workspace)
        ids = {
            register.register(
                RegisterRiskCommandV1(
                    task_id="task-loop",
                    title=f"risk {i}",
                    severity=RiskSeverity.LOW,
                    owner="chief_engineer",
                    mitigation="m",
                    workspace=self.workspace,
                )
            ).risk_id
            for i in range(50)
        }
        # The uuid suffix guarantees no collision even within the same microsecond.
        self.assertEqual(len(ids), 50)

    def test_load_tolerates_invalid_enum_on_disk(self) -> None:
        # A persisted record with a bogus severity must not crash list();
        # the loader coerces to a safe default rather than raising.
        register = RiskRegister(self.workspace)
        record = register.register(
            RegisterRiskCommandV1(
                task_id="task-x",
                title="t",
                severity=RiskSeverity.HIGH,
                owner="chief_engineer",
                mitigation="m",
                workspace=self.workspace,
            )
        )
        path = Path(resolve_logical_path(self.workspace, "runtime/risks")) / f"{record.risk_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["severity"] = "apocalyptic"
        path.write_text(json.dumps(data), encoding="utf-8")
        listed = register.list()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].severity, RiskSeverity.MEDIUM)

    def test_build_risk_event_stamps_at(self) -> None:
        event = build_risk_event(
            risk_id="risk_x",
            workspace=self.workspace,
            action="registered",
            actor="chief_engineer",
            note="initial",
        )
        self.assertTrue(event.event_id.startswith("riskevt_"))
        self.assertEqual(event.action, "registered")
        self.assertEqual(event.note, "initial")
        self.assertTrue(event.at.endswith("Z"))


if __name__ == "__main__":
    unittest.main()
