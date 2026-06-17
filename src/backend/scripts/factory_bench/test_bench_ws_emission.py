"""Unit tests for factory-bench real-time WS event emission.

The bench process drives L1-L8 projects sequentially and must emit progress
events to the workspace's runtime.events.jsonl so the WebSocket bridge
(``/v2/ws/runtime``) can stream them to the ContextOS real-time dashboard.

These tests lock down the contract:
  * the helper resolves cache_root + latest_run.json -> events path;
  * the helper writes a JSONL line in the same schema the WS server tails
    (kind=event, actor=factory-bench, name=factory_bench.<event>);
  * the helper is a no-op (returns False) when no run_id is available yet
    (the WS server only tails the latest run).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "/home/dains/Documents/polaris/src/backend")

from scripts.factory_bench.run_factory_bench import _emit_bench_event


class TestBenchEventEmission(unittest.TestCase):
    def test_emit_writes_to_latest_run_events_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cache_root = td_path / "cache"
            run_id = "test-run-001"
            (cache_root / "runs" / run_id / "events").mkdir(parents=True)
            (cache_root / "latest_run.json").write_text(
                json.dumps({"run_id": run_id, "path": f"runs/{run_id}"}),
                encoding="utf-8",
            )

            ok = _emit_bench_event(
                workspace=td_path / "workspace",
                project_id="L1-01",
                level=1,
                name="project.started",
                summary="L1-01 starting",
                cache_root=str(cache_root),
            )
            self.assertTrue(ok, "_emit_bench_event must succeed when latest_run.json is set")

            events_file = cache_root / "runs" / run_id / "events" / "runtime.events.jsonl"
            self.assertTrue(events_file.is_file(), "runtime.events.jsonl must be created")
            lines = events_file.read_text(encoding="utf-8").strip().split("\n")
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            # Schema fields consumed by the WS bridge + ContextOS dashboard.
            self.assertEqual(record["kind"], "event")
            self.assertEqual(record["actor"], "factory-bench")
            self.assertEqual(record["name"], "factory_bench.project.started")
            self.assertEqual(record["summary"], "L1-01 starting")
            self.assertEqual(record["meta"]["project_id"], "L1-01")
            self.assertEqual(record["meta"]["level"], 1)
            self.assertEqual(record["meta"]["source"], "factory-bench")

    def test_emit_returns_false_when_no_latest_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cache_root = td_path / "cache"
            cache_root.mkdir(parents=True)
            ok = _emit_bench_event(
                workspace=td_path,
                project_id="L1-01",
                level=1,
                name="project.started",
                cache_root=str(cache_root),
            )
            self.assertFalse(ok, "no latest_run.json -> no run to write into")

    def test_emit_appends_multiple_events_with_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            cache_root = td_path / "cache"
            run_id = "test-run-002"
            (cache_root / "runs" / run_id / "events").mkdir(parents=True)
            (cache_root / "latest_run.json").write_text(
                json.dumps({"run_id": run_id}),
                encoding="utf-8",
            )

            for name in ("project.started", "project.completed", "gate.evaluated"):
                _emit_bench_event(
                    workspace=td_path,
                    project_id="L2-07",
                    level=2,
                    name=name,
                    cache_root=str(cache_root),
                )

            events_file = cache_root / "runs" / run_id / "events" / "runtime.events.jsonl"
            lines = events_file.read_text(encoding="utf-8").strip().split("\n")
            self.assertEqual(len(lines), 3)
            records = [json.loads(line) for line in lines]
            names = [r["name"] for r in records]
            self.assertEqual(
                names,
                [
                    "factory_bench.project.started",
                    "factory_bench.project.completed",
                    "factory_bench.gate.evaluated",
                ],
            )
            for r in records:
                self.assertEqual(r["actor"], "factory-bench")
                self.assertEqual(r["meta"]["project_id"], "L2-07")
                self.assertEqual(r["meta"]["level"], 2)


if __name__ == "__main__":
    unittest.main()
