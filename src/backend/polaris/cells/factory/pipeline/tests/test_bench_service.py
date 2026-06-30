"""Unit tests for the workspace-agnostic FactoryBenchService."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path


class TestFactoryBenchService(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        os.environ["FACTORY_BENCH_SESSIONS_ROOT"] = str(self.root)
        from polaris.cells.factory.pipeline.internal.bench_service import FactoryBenchService

        self.svc = FactoryBenchService(root=self.root)

    def _read_jsonl(self, path: Path) -> list[dict]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def test_register_session_writes_status_and_events(self) -> None:
        sid = self.svc.register_session(
            work_dir="/tmp/ws",
            project_ids=["L1-01", "L2-07"],
            total=2,
        )
        self.assertTrue(sid.startswith("bench-"))
        sdir = self.root / sid
        self.assertTrue((sdir / "status.json").is_file())
        self.assertTrue((sdir / "events.jsonl").is_file())
        status = json.loads((sdir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["project_ids"], ["L1-01", "L2-07"])
        self.assertEqual(status["total"], 2)
        self.assertEqual(status["status"], "running")

    def test_register_session_rejects_duplicate(self) -> None:
        sid = self.svc.register_session(work_dir="/tmp/ws", project_ids=["L1-01"], total=1)
        with self.assertRaises(FileExistsError):
            self.svc.register_session(work_dir="/tmp/ws", project_ids=["L1-02"], total=1, session_id=sid)

    def test_register_session_rejects_path_traversal_id(self) -> None:
        # Path-traversal ids are rejected.
        with self.assertRaises(ValueError):
            self.svc.register_session(work_dir="/tmp/ws", project_ids=[], total=0, session_id="../escape")
        with self.assertRaises(ValueError):
            self.svc.register_session(work_dir="/tmp/ws", project_ids=[], total=0, session_id="sub/dir")
        with self.assertRaises(ValueError):
            self.svc.register_session(work_dir="/tmp/ws", project_ids=[], total=0, session_id=".hidden")
        # Empty session_id means "generate a default", which is a valid path
        # through the service (used by the bench subprocess).
        sid = self.svc.register_session(work_dir="/tmp/ws", project_ids=[], total=0, session_id="")
        self.assertTrue(sid.startswith("bench-"))

    def test_append_event_writes_to_events_jsonl(self) -> None:
        sid = self.svc.register_session(work_dir="/tmp/ws", project_ids=["L1-01"], total=1)
        ok1 = self.svc.append_event(
            sid,
            {"type": "project.started", "name": "L1-01", "actor": "factory-bench"},
        )
        ok2 = self.svc.append_event(
            sid,
            {"type": "project.completed", "name": "L1-01", "actor": "factory-bench", "ok": True},
        )
        self.assertTrue(ok1 and ok2)
        events = self._read_jsonl(self.root / sid / "events.jsonl")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["type"], "project.started")
        self.assertEqual(events[0]["session_id"], sid)
        self.assertEqual(events[1]["type"], "project.completed")
        self.assertTrue(events[1]["ok"])

    def test_append_event_returns_false_for_unknown_session(self) -> None:
        ok = self.svc.append_event("bench-doesnotexist-xyz", {"type": "noop"})
        self.assertFalse(ok)

    def test_complete_session_marks_terminal_status(self) -> None:
        sid = self.svc.register_session(work_dir="/tmp/ws", project_ids=["L1-01"], total=1)
        self.svc.complete_session(sid, success=True, summary={"passed": 1, "failed": 0})
        status = json.loads((self.root / sid / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "completed")
        self.assertIn("completed_at", status)
        self.assertEqual(status["metadata"]["passed"], 1)

    def test_complete_session_failure_marks_failed(self) -> None:
        sid = self.svc.register_session(work_dir="/tmp/ws", project_ids=["L1-01"], total=1)
        self.svc.complete_session(sid, success=False)
        status = json.loads((self.root / sid / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "failed")

    def test_get_session_returns_status_and_recent_events(self) -> None:
        sid = self.svc.register_session(work_dir="/tmp/ws", project_ids=["L1-01", "L2-07"], total=2)
        for i in range(3):
            self.svc.append_event(sid, {"type": f"event.{i}"})
        snapshot = self.svc.get_session(sid)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["session_id"], sid)
        self.assertEqual(snapshot["total"], 2)
        self.assertEqual(len(snapshot["events"]), 3)
        self.assertEqual(snapshot["events"][0]["type"], "event.0")
        self.assertEqual(snapshot["control_plane_projection"]["source"], "run_ledger_projection")
        self.assertEqual(snapshot["control_plane_projection"]["status"], "pending")

    def test_list_sessions_returns_recent_first(self) -> None:
        sids: list[str] = []
        for i in range(3):
            sids.append(self.svc.register_session(work_dir=f"/tmp/ws{i}", project_ids=[f"L{i}"], total=1))
            # Force a strictly increasing mtime so the sort is stable even on
            # filesystems with second-resolution timestamps.
            time.sleep(1.05)
        listed = self.svc.list_sessions()
        self.assertEqual([s["session_id"] for s in listed], list(reversed(sids)))

    def test_session_snapshots_include_run_ledger_control_plane_projection(self) -> None:
        work_dir = self.root / "bench-work"
        work_dir.mkdir()
        (work_dir / "factory_audits.json").write_text(
            json.dumps(
                {
                    "goal_audit": {
                        "run_ledger": {
                            "projected": 1,
                            "total": 1,
                            "missing": 0,
                        }
                    },
                    "records": [
                        {
                            "project_id": "L1-01",
                            "requested_project_id": "L1-01",
                            "canonical_project_id": "L1-11",
                            "instance_id": "bench-instance-1",
                            "workspace": "/tmp/factory-bench/L1-01",
                            "backend_port": 51001,
                            "frontend_port": 52001,
                            "run_id": "bench-run-1",
                            "factory_run_id": "factory-run-1",
                            "run_ledger_projection": {
                                "schema_version": 1,
                                "source": "run_ledger",
                                "ok": True,
                                "integrity_ok": True,
                                "outcome_ok": True,
                                "event_count": 1,
                                "gate_count": 1,
                                "missing": [],
                                "gates": [],
                                "failed_gates": [],
                                "capability": {
                                    "ok": True,
                                    "issues": [],
                                    "latest_token_id": "job-token-1",
                                },
                                "physical_evidence": {
                                    "command_count": 2,
                                    "sampled_command_count": 2,
                                    "truncated_command_events": 0,
                                },
                                "evidence_policy": {
                                    "ok": True,
                                    "enabled_modalities": ["browser"],
                                    "required_modalities": [],
                                    "missing_required_modalities": [],
                                },
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        sid = self.svc.register_session(
            work_dir=str(work_dir), project_ids=["L1-01"], total=1, session_id="bench-ledger"
        )

        snapshot = self.svc.get_session(sid)
        listed = self.svc.list_sessions()

        assert snapshot is not None
        projection = snapshot["control_plane_projection"]
        self.assertTrue(projection["ok"])
        self.assertEqual(projection["status"], "ready")
        self.assertEqual(projection["source"], "run_ledger_projection")
        self.assertEqual(projection["total"], 1)
        self.assertEqual(projection["projected"], 1)
        self.assertEqual(projection["missing"], 0)
        self.assertEqual(projection["projects"][0]["project_id"], "L1-01")
        self.assertEqual(projection["projects"][0]["requested_project_id"], "L1-01")
        self.assertEqual(projection["projects"][0]["canonical_project_id"], "L1-11")
        self.assertEqual(projection["projects"][0]["instance_id"], "bench-instance-1")
        self.assertEqual(projection["projects"][0]["workspace"], "/tmp/factory-bench/L1-01")
        self.assertEqual(projection["projects"][0]["backend_port"], 51001)
        self.assertEqual(projection["projects"][0]["frontend_port"], 52001)
        self.assertEqual(projection["projects"][0]["run_id"], "bench-run-1")
        self.assertEqual(projection["projects"][0]["factory_run_id"], "factory-run-1")
        self.assertEqual(projection["projects"][0]["latest_token_id"], "job-token-1")
        self.assertEqual(projection["projects"][0]["run_ledger_projection"]["source"], "run_ledger")
        self.assertEqual(projection["evidence_policy"]["enabled_modalities"], ["browser"])
        self.assertEqual(projection["projects"][0]["evidence_policy"]["enabled_modalities"], ["browser"])
        self.assertEqual(projection["goal_audit"], {"projected": 1, "total": 1, "missing": 0})
        self.assertEqual(listed[0]["control_plane_projection"], projection)

    def test_session_snapshots_normalize_legacy_failed_evidence_projection(self) -> None:
        work_dir = self.root / "bench-work-legacy"
        work_dir.mkdir()
        (work_dir / "factory_audits.json").write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "project_id": "L1-04",
                            "run_ledger_projection": {
                                "schema_version": 1,
                                "source": "run_ledger",
                                "ok": False,
                                "integrity_ok": False,
                                "outcome_ok": False,
                                "event_count": 1,
                                "gate_count": 1,
                                "missing": ["command"],
                                "gates": [],
                                "failed_gates": [],
                                "capability": {
                                    "ok": True,
                                    "issues": [],
                                    "latest_token_id": "job-token-legacy",
                                },
                                "evidence_policy": {
                                    "ok": False,
                                    "enabled_modalities": [],
                                    "required_modalities": ["code", "command"],
                                    "missing_required_modalities": ["command"],
                                },
                                "evidence_modalities": {
                                    "code": {
                                        "total": 1,
                                        "present": 1,
                                        "ok": 1,
                                        "failed": 0,
                                        "latest_detail": "files landed",
                                    },
                                    "command": {
                                        "total": 1,
                                        "present": 1,
                                        "ok": 0,
                                        "failed": 1,
                                        "latest_detail": "go test failed",
                                    },
                                },
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        sid = self.svc.register_session(
            work_dir=str(work_dir), project_ids=["L1-04"], total=1, session_id="bench-ledger-legacy"
        )

        snapshot = self.svc.get_session(sid)

        assert snapshot is not None
        projection = snapshot["control_plane_projection"]
        project = projection["projects"][0]
        self.assertFalse(projection["ok"])
        self.assertFalse(project["ok"])
        self.assertTrue(project["integrity_ok"])
        self.assertFalse(project["outcome_ok"])
        self.assertEqual(project["missing"], [])
        self.assertEqual(project["failed_required_modalities"], ["command"])
        self.assertEqual(project["detail"], "run ledger projection required evidence failed: command")
        self.assertEqual(project["evidence_policy"]["missing_required_modalities"], [])
        self.assertEqual(project["evidence_policy"]["failed_required_modalities"], ["command"])
        self.assertEqual(projection["evidence_policy"]["missing_required_modalities"], [])
        self.assertEqual(projection["evidence_policy"]["failed_required_modalities"], ["command"])

    def test_read_events_from_returns_all_events_from_offset(self) -> None:
        sid = self.svc.register_session(work_dir="/tmp/ws", project_ids=["L1-01"], total=1)
        for i in range(5):
            self.svc.append_event(sid, {"type": f"event.{i}"})
        events_path = self.root / sid / "events.jsonl"
        # From offset 0 -> all 5 events.
        events, offset = self.svc.read_events_from(sid, start_offset=0)
        self.assertEqual([e["type"] for e in events], ["event.0", "event.1", "event.2", "event.3", "event.4"])
        self.assertEqual(offset, events_path.stat().st_size)
        # From current offset -> no events (nothing appended since).
        events2, offset2 = self.svc.read_events_from(sid, start_offset=offset)
        self.assertEqual(events2, [])
        self.assertEqual(offset2, offset)
        # Append more events, read from the saved offset -> just the new ones.
        for i in range(5, 7):
            self.svc.append_event(sid, {"type": f"event.{i}"})
        events3, offset3 = self.svc.read_events_from(sid, start_offset=offset)
        self.assertEqual([e["type"] for e in events3], ["event.5", "event.6"])
        self.assertEqual(offset3, events_path.stat().st_size)

    def test_update_progress_sets_completed_and_failed(self) -> None:
        sid = self.svc.register_session(work_dir="/tmp/ws", project_ids=["L1-01", "L2-07"], total=2)
        ok = self.svc.update_progress(sid, completed=1, failed=0)
        self.assertTrue(ok)
        snapshot = self.svc.get_session(sid)
        self.assertEqual(snapshot["completed"], 1)
        self.assertEqual(snapshot["failed"], 0)

    def test_update_progress_partial(self) -> None:
        sid = self.svc.register_session(work_dir="/tmp/ws", project_ids=["L1-01"], total=1)
        ok = self.svc.update_progress(sid, failed=1)
        self.assertTrue(ok)
        snapshot = self.svc.get_session(sid)
        self.assertEqual(snapshot["completed"], 0)
        self.assertEqual(snapshot["failed"], 1)

    def test_update_progress_rejects_unknown_session(self) -> None:
        ok = self.svc.update_progress("bench-missing-xyz", completed=1)
        self.assertFalse(ok)

    def test_complete_session_honours_summary_counters(self) -> None:
        sid = self.svc.register_session(work_dir="/tmp/ws", project_ids=["L1-01", "L2-07"], total=2)
        self.svc.update_progress(sid, completed=1, failed=1)
        self.svc.complete_session(sid, success=False, summary={"completed": 1, "failed": 1, "total": 2})
        snapshot = self.svc.get_session(sid)
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["completed"], 1)
        self.assertEqual(snapshot["failed"], 1)
        self.assertEqual(snapshot["metadata"]["total"], 2)


if __name__ == "__main__":
    unittest.main()
