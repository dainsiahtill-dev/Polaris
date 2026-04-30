import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


def _load_module(rel_path: str, name: str):
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "src" / "backend" / "core" / "polaris_loop" / rel_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load {rel_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_evidence():
    return _load_module("director_evidence.py", "director_evidence")


def _load_trajectory():
    return _load_module("director_trajectory.py", "director_trajectory")


class TestEvidenceAndTrajectory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence = _load_evidence()
        cls.trajectory = _load_trajectory()

    def test_summarize_tool_outputs(self):
        outputs = [
            {"tool": "repo_rg", "pattern": "foo"},
            {"tool": "repo_read_around", "file": "a.py", "start_line": 1, "end_line": 3},
        ]
        summary = self.evidence.summarize_tool_outputs(outputs)
        self.assertEqual(summary["rg_queries"], ["foo"])
        self.assertEqual(summary["evidence_refs"][0]["file"], "a.py")

    def test_build_evidence_summary(self):
        outputs = [
            {
                "tool": "repo_read_around",
                "file": "a.py",
                "start_line": 1,
                "end_line": 3,
                "truncated": False,
                "content": [{"n": 1, "t": "line"}],
            }
        ]
        summary = self.evidence.build_evidence_summary(outputs, "summary")
        self.assertEqual(summary[0]["line_count"], 1)
        self.assertIn("hash", summary[0])

    def test_write_evidence_package(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = SimpleNamespace(
                workspace_full=temp_dir,
                log_full="",
                evidence_verbosity="summary",
            )
            outputs = [
                {
                    "tool": "repo_read_around",
                    "file": "a.py",
                    "start_line": 1,
                    "end_line": 3,
                    "truncated": False,
                    "content": [{"n": 1, "t": "line"}],
                }
            ]
            path = self.evidence.write_evidence_package(state, outputs, "TASK", 1, 1, 1, 10)
            self.assertTrue(path)
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(payload["task_id"], "TASK")

    def test_write_trajectory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = SimpleNamespace(
                cache_root_full="",
                workspace_full=temp_dir,
                director_result_full="dir.json",
                qa_full="qa.md",
                planner_full="plan.md",
                ollama_full="ollama.md",
                reviewer_full="review.md",
                events_full="events.jsonl",
                pm_task_path="pm.json",
                policy_path="policy.json",
            )
            result_payload = {"status": "success", "tool_rounds": 1, "total_lines_read": 10, "patch_risk": {"score": 1}}
            policy_effective = {"version": 1}
            path = self.trajectory.write_trajectory(
                state,
                run_id="dir-00001",
                task_id="T1",
                task_fingerprint="F1",
                pm_iteration=1,
                director_iteration=1,
                result_payload=result_payload,
                policy_effective=policy_effective,
                evidence_path="evidence.json",
                event_seq_start=1,
                event_seq_end=2,
            )
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(payload["run_id"], "dir-00001")
            self.assertEqual(payload["event_span"]["count"], 2)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
