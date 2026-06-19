"""Tests for the Tech Radar ledger + stack-policy check."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from polaris.cells.chief_engineer.blueprint.internal.tech_radar import (
    TechRadarLedger,
    build_tech_radar_event,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ListTechRadarQueryV1,
    RegisterTechRadarCommandV1,
    TechRadarRing,
    UpdateTechRadarRingCommandV1,
)
from polaris.cells.chief_engineer.blueprint.public.service import (
    check_stack_policy,
    list_tech_radar,
    register_tech_radar,
    summarize_tech_radar,
    update_tech_radar_ring,
)
from polaris.kernelone.storage import resolve_logical_path


class TestTechRadarLedger(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_register_persists(self) -> None:
        radar = TechRadarLedger(self.workspace)
        record = radar.register(
            RegisterTechRadarCommandV1(
                library="moment.js",
                ring=TechRadarRing.HOLD,
                owner="chief_engineer",
                workspace=self.workspace,
                rationale="unmaintained; prefer date-fns",
            )
        )
        self.assertTrue(record.entry_id.startswith("radar_"))
        self.assertEqual(record.ring, TechRadarRing.HOLD)
        path = Path(resolve_logical_path(self.workspace, "runtime/tech_radar")) / f"{record.entry_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["ring"], "hold")
        self.assertEqual(data["library"], "moment.js")

    def test_update_ring_appends_history(self) -> None:
        radar = TechRadarLedger(self.workspace)
        record = radar.register(
            RegisterTechRadarCommandV1(
                library="redux",
                ring=TechRadarRing.TRIAL,
                owner="ce",
                workspace=self.workspace,
            )
        )
        updated = radar.update_ring(
            UpdateTechRadarRingCommandV1(
                workspace=self.workspace,
                entry_id=record.entry_id,
                ring=TechRadarRing.ADOPT,
                note="proven in 3 services",
            ),
            actor="ce",
        )
        self.assertEqual(updated.ring, TechRadarRing.ADOPT)
        self.assertEqual(updated.history[-1]["note"], "proven in 3 services")

    def test_check_stack_policy_flags_hold_and_deprecated(self) -> None:
        radar = TechRadarLedger(self.workspace)
        radar.register(
            RegisterTechRadarCommandV1(
                library="moment.js", ring=TechRadarRing.HOLD, owner="ce", workspace=self.workspace
            )
        )
        radar.register(
            RegisterTechRadarCommandV1(
                library="bower", ring=TechRadarRing.DEPRECATED, owner="ce", workspace=self.workspace
            )
        )
        radar.register(
            RegisterTechRadarCommandV1(library="react", ring=TechRadarRing.ADOPT, owner="ce", workspace=self.workspace)
        )
        violations = radar.check_stack_policy(["React", "moment.js", "bower", "vue"])
        libs = {v.library for v in violations}
        self.assertEqual(libs, {"moment.js", "bower"})
        # 'react' is adopt (allowed); 'vue' is unknown (not flagged).

    def test_check_stack_policy_latest_ring_wins(self) -> None:
        radar = TechRadarLedger(self.workspace)
        first = radar.register(
            RegisterTechRadarCommandV1(library="webpack", ring=TechRadarRing.HOLD, owner="ce", workspace=self.workspace)
        )
        # Move it back to adopt -> no longer a violation.
        radar.update_ring(
            UpdateTechRadarRingCommandV1(
                workspace=self.workspace,
                entry_id=first.entry_id,
                ring=TechRadarRing.ADOPT,
            ),
            actor="ce",
        )
        violations = radar.check_stack_policy(["webpack"])
        self.assertEqual(violations, [])

    def test_path_traversal_rejected(self) -> None:
        radar = TechRadarLedger(self.workspace)
        for evil in ("../../etc/passwd", "a/b", ".."):
            with self.assertRaises(ValueError):
                radar.load(evil)
        with self.assertRaises(ValueError):
            radar.update_ring(
                UpdateTechRadarRingCommandV1(
                    workspace=self.workspace,
                    entry_id="../../x",
                    ring=TechRadarRing.ADOPT,
                ),
                actor="ce",
            )

    def test_supersede_deprecates_predecessor(self) -> None:
        radar = TechRadarLedger(self.workspace)
        first = radar.register(
            RegisterTechRadarCommandV1(library="enzyme", ring=TechRadarRing.ADOPT, owner="ce", workspace=self.workspace)
        )
        radar.register(
            RegisterTechRadarCommandV1(
                library="@testing-library/react",
                ring=TechRadarRing.ADOPT,
                owner="ce",
                workspace=self.workspace,
                supersedes=first.entry_id,
            )
        )
        reloaded = radar.load(first.entry_id)
        assert reloaded is not None
        self.assertEqual(reloaded.ring, TechRadarRing.DEPRECATED)

    def test_load_tolerates_invalid_ring_on_disk(self) -> None:
        radar = TechRadarLedger(self.workspace)
        record = radar.register(
            RegisterTechRadarCommandV1(library="x", ring=TechRadarRing.ADOPT, owner="ce", workspace=self.workspace)
        )
        path = Path(resolve_logical_path(self.workspace, "runtime/tech_radar")) / f"{record.entry_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["ring"] = "bogus"
        path.write_text(json.dumps(data), encoding="utf-8")
        listed = radar.list()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].ring, TechRadarRing.ADOPT)

    def test_check_stack_policy_decided_at_tie_is_deterministic(self) -> None:
        # Two separate entries for the same library with an IDENTICAL on-disk
        # decided_at must resolve deterministically (entry_id breaks the tie),
        # independent of directory iteration order.
        radar = TechRadarLedger(self.workspace)
        a = radar.register(
            RegisterTechRadarCommandV1(library="samelib", ring=TechRadarRing.HOLD, owner="ce", workspace=self.workspace)
        )
        b = radar.register(
            RegisterTechRadarCommandV1(
                library="samelib", ring=TechRadarRing.ADOPT, owner="ce", workspace=self.workspace
            )
        )
        radar_dir = Path(resolve_logical_path(self.workspace, "runtime/tech_radar"))
        frozen = "2026-06-17T00:00:00Z"
        for entry_id in (a.entry_id, b.entry_id):
            path = radar_dir / f"{entry_id}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["decided_at"] = frozen
            path.write_text(json.dumps(data), encoding="utf-8")

        # The higher entry_id wins the tie deterministically.
        winner_ring = TechRadarRing.ADOPT if b.entry_id > a.entry_id else TechRadarRing.HOLD
        violations = radar.check_stack_policy(["samelib"])
        if winner_ring == TechRadarRing.ADOPT:
            self.assertEqual(violations, [])
        else:
            self.assertEqual([v.library for v in violations], ["samelib"])
        # Stable across repeated calls.
        self.assertEqual(radar.check_stack_policy(["samelib"]), violations)

    def test_build_event(self) -> None:
        event = build_tech_radar_event(entry_id="radar_x", workspace=self.workspace, action="ring:hold", actor="ce")
        self.assertTrue(event.event_id.startswith("radarevt_"))


class TestTechRadarServiceSurface(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_service_round_trip_and_policy(self) -> None:
        record = register_tech_radar(
            RegisterTechRadarCommandV1(
                library="jquery",
                ring=TechRadarRing.DEPRECATED,
                owner="chief_engineer",
                workspace=self.workspace,
                rationale="legacy DOM library",
            )
        )
        listed = list_tech_radar(ListTechRadarQueryV1(workspace=self.workspace, ring=TechRadarRing.DEPRECATED))
        self.assertEqual([r.entry_id for r in listed], [record.entry_id])
        update_tech_radar_ring(
            UpdateTechRadarRingCommandV1(
                workspace=self.workspace,
                entry_id=record.entry_id,
                ring=TechRadarRing.HOLD,
            )
        )
        summary = summarize_tech_radar(self.workspace)
        self.assertEqual(summary["by_ring"]["hold"], 1)
        violations = check_stack_policy(self.workspace, ["jQuery"])
        self.assertEqual([v.library for v in violations], ["jquery"])


if __name__ == "__main__":
    unittest.main()
