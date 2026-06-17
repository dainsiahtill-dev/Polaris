"""Tests for the rollback-link builder."""

from __future__ import annotations

import os
import tempfile
import unittest

from polaris.cells.chief_engineer.blueprint.internal.rollback_link import (
    build_rollback_link,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    RiskRecordV1,
    RiskSeverity,
    RiskStatus,
    RollbackStrategy,
)


class TestRollbackLink(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_git_workspace_picks_git_revert(self) -> None:
        os.makedirs(os.path.join(self.workspace, ".git"))
        link = build_rollback_link(
            workspace=self.workspace,
            blueprint_id="ce_x",
            blueprint={"target_files": ["a.py"]},
        )
        self.assertTrue(link.enabled)
        self.assertEqual(link.strategy, RollbackStrategy.GIT_REVERT)
        self.assertTrue(link.marker_path.endswith("ce_x.stash"))
        self.assertIn("blueprint_persisted", link.preconditions)

    def test_non_git_workspace_picks_file_snapshot(self) -> None:
        link = build_rollback_link(
            workspace=self.workspace,
            blueprint_id="ce_y",
            blueprint={"target_files": ["a.py"]},
        )
        self.assertEqual(link.strategy, RollbackStrategy.FILE_SNAPSHOT)

    def test_open_blocker_omits_satisfied_precondition(self) -> None:
        # preconditions list SATISFIED checks: an open blocker risk means
        # "no_blocker_risks_open" is NOT satisfied, so it must be ABSENT.
        risks = [
            RiskRecordV1(
                risk_id="r1",
                task_id="t1",
                title="t",
                severity=RiskSeverity.BLOCKER,
                owner="chief_engineer",
                mitigation="m",
                status=RiskStatus.OPEN,
                detected_at="2026-06-17T00:00:00Z",
            )
        ]
        link = build_rollback_link(
            workspace=self.workspace,
            blueprint_id="ce_z",
            blueprint={"target_files": ["a.py"]},
            risks=risks,
        )
        self.assertNotIn("no_blocker_risks_open", link.preconditions)

    def test_no_open_blocker_lists_satisfied_precondition(self) -> None:
        # No open blocker risk => the check holds => it IS listed.
        link = build_rollback_link(
            workspace=self.workspace,
            blueprint_id="ce_ok",
            blueprint={"target_files": ["a.py"]},
            risks=[],
        )
        self.assertIn("no_blocker_risks_open", link.preconditions)
        self.assertIn("target_files_declared", link.preconditions)

    def test_no_targets_disables(self) -> None:
        link = build_rollback_link(
            workspace=self.workspace,
            blueprint_id="ce_n",
            blueprint={},
        )
        self.assertFalse(link.enabled)
        # No declared targets => "target_files_declared" is NOT satisfied => absent.
        self.assertNotIn("target_files_declared", link.preconditions)

    def test_marker_path_under_state_dir(self) -> None:
        link = build_rollback_link(
            workspace=self.workspace,
            blueprint_id="ce_m",
            blueprint={"target_files": ["a.py"]},
        )
        self.assertIn("runtime/state/blueprints", link.marker_path)
        self.assertTrue(link.marker_path.endswith("ce_m.stash"))


if __name__ == "__main__":
    unittest.main()
