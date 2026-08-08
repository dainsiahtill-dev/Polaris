"""Unit tests for batch_report.py — synthetic minimal audit JSON."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.factory_bench.batch_report import _aggregate, _classify_chain_log, _update_history, main


def _write_audit(work_dir: Path, records: list[dict]) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "factory_audits.json").write_text(
        json.dumps({"schema_version": "factory-audit/1", "records": records}, ensure_ascii=False),
        encoding="utf-8",
    )


def _record(pid: str, all_passed: bool, task_market_ok: bool, dur: float, chain_log_text: str | None = None) -> dict:
    requirements = {
        "artifact_landed": {"ok": all_passed},
        "environment_prepared": {"ok": all_passed},
        "build_test_lint_ran": {"ok": all_passed},
        "entrypoint_smoke": {"ok": all_passed},
    }
    r = {
        "schema_version": "factory-audit/1",
        "project_id": pid,
        "level": 2,
        "all_checks_passed": all_passed,
        "chain": {
            "exit_code": 0 if (all_passed and task_market_ok) else 1,
            "duration_s": dur,
            "task_market_exit_code": 0 if task_market_ok else 1,
        },
        "checks": [
            {"check": "py_compile", "ok": True, "detail": "ok"},
            {"check": "min_files:2", "ok": True, "detail": "ok"},
        ],
        "real_run_gate": {"ok": all_passed, "requirements": requirements},
        "llm_route_audit": {"ok": all_passed},
    }
    return r


class TestBatchReport(unittest.TestCase):
    def test_aggregate_runnable_and_step_rate(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            records = [
                _record("L2-A", all_passed=True, task_market_ok=True, dur=100.0),
                _record("L2-B", all_passed=False, task_market_ok=False, dur=200.0),
            ]
            _write_audit(d, records)
            report = _aggregate(records, d)
            self.assertEqual(report["runnable"]["passed"], 1)
            self.assertEqual(report["runnable"]["total"], 2)
            self.assertEqual(report["runnable"]["rate"], 0.5)
            # 2 records, each with 2 checks + 1 task_market turn = 3 attempted each
            self.assertEqual(report["steps"]["attempted"], 6)
            # A: 2 checks + 1 tm = 3 pass; B: 2 checks + 0 tm = 2 pass
            self.assertEqual(report["steps"]["passed"], 5)
            self.assertAlmostEqual(report["step_success_rate"], 0.833, places=3)
            self.assertEqual(report["wall"]["max_s"], 200.0)
            self.assertEqual(report["wall"]["sum_s"], 300.0)
            self.assertEqual(report["by_level"], {2: {"total": 2, "passed": 1}})
            self.assertEqual(report["real_run_gate"]["passed"], 1)
            self.assertEqual(report["llm_route_audit"]["passed"], 1)
            self.assertEqual(report["completion_contract"]["passed"], 1)
            self.assertEqual(report["completion_contract"]["requirements"]["artifact_landed"]["passed"], 1)
            self.assertEqual(report["completion_contract"]["requirements"]["environment_prepared"]["passed"], 1)
            self.assertEqual(report["completion_contract"]["requirements"]["build_test_lint_ran"]["passed"], 1)
            self.assertEqual(report["completion_contract"]["requirements"]["entrypoint_smoke"]["passed"], 1)

    def test_aggregate_prefers_structured_failure_taxonomy(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            record = _record("L2-C", all_passed=False, task_market_ok=False, dur=10.0)
            record["failure_taxonomy"] = {
                "ok": False,
                "category": "target_project_baseline",
                "root_cause_signature": "target_project_baseline:real_run_gate.entrypoint_smoke",
            }
            report = _aggregate([record], d)
            self.assertEqual(
                report["root_cause_tally"].get(
                    "[target_project_baseline ] target_project_baseline:real_run_gate.entrypoint_smoke"
                ),
                1,
            )

    def test_classify_chain_log_picks_attribution_categories(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            log = d / "X.chain.log"
            log.write_text(
                "RuntimeError: single_batch_contract_violation: mutation write target drift\n"
                "[invoker] reasoning-truncation re-ask: reserved output budget\n"
                "PreWriteGuard: Code syntax validation failed: line 5\n"
                "Instructor not installed, using fallback\n",
                encoding="utf-8",
            )
            counts = _classify_chain_log(log)
            self.assertEqual(counts.get("[platform_fixable  ] single_batch_contract_violation"), 1)
            self.assertEqual(counts.get("[model_output_candidate] reasoning_truncation_reask"), 1)
            self.assertEqual(counts.get("[working_as_intended] PreWriteGuard_blocked"), 1)
            self.assertEqual(counts.get("[post_failure_noise] Instructor_fallback"), 1)
            # The dark-launched symbol-coherence error must be detected
            log2 = d / "Y.chain.log"
            log2.write_text(
                "Artifact quality scan failed: unresolved import symbol 'HTTPClient' from 'common.http_client' in common/__init__.py\n",
                encoding="utf-8",
            )
            counts2 = _classify_chain_log(log2)
            self.assertEqual(counts2.get("[platform_fixable  ] unresolved_import_symbol"), 1)

    def test_main_returns_2_for_missing_workdir(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(main([str(Path(td) / "does-not-exist")]), 2)

    def test_main_returns_1_for_empty_workdir(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            # tempfile.TemporaryDirectory already created the dir; just point at it.
            self.assertEqual(main([td]), 1)

    def test_main_human_report_on_real_data(self) -> None:
        import contextlib
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write_audit(d, [_record("L2-T", all_passed=True, task_market_ok=True, dur=42.0)])
            # Also write a chain.log so the root-cause tally has something to count
            (d / "L2-T.chain.log").write_text(
                "RuntimeError: single_batch_contract_violation: no write tool\n", encoding="utf-8"
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = main([str(d)])
            self.assertEqual(code, 0)
            out = buf.getvalue()
            self.assertIn("L2: 1/1", out)
            self.assertIn("single_batch_contract_violation", out)
            self.assertIn("42.0", out)
            self.assertIn("real-run gate", out)
            self.assertIn("completion gate", out)
            self.assertIn("artifact_landed", out)

    def test_update_history_tracks_new_root_cause_streak(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            history_path = Path(td) / "root_cause_history.json"
            report = {"root_cause_tally": {"[llm_output              ] llm_output:llm_route_audit": 1}}
            first = _update_history(report, history_path, batch_id="b1")
            self.assertEqual(first["last_new_root_causes"], ["[llm_output              ] llm_output:llm_route_audit"])
            self.assertEqual(first["streak_without_new_common_root_causes"], 0)

            second = _update_history(report, history_path, batch_id="b2")
            self.assertEqual(second["last_new_root_causes"], [])
            self.assertEqual(second["streak_without_new_common_root_causes"], 1)

    def test_main_stable_batches_gate_requires_history(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write_audit(d, [_record("L2-T", all_passed=True, task_market_ok=True, dur=1.0)])
            self.assertEqual(main([str(d), "--require-stable-batches", "1"]), 2)

    def test_main_stable_batches_gate_fails_until_streak_reaches_requirement(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            history = d / "root_cause_history.json"
            record = _record("L2-F", all_passed=False, task_market_ok=False, dur=1.0)
            record["failure_taxonomy"] = {
                "ok": False,
                "category": "chief_engineer_blueprint",
                "root_cause_signature": "chief_engineer_blueprint:missing_or_invalid_blueprint",
            }
            _write_audit(d, [record])

            self.assertEqual(
                main([str(d), "--history", str(history), "--batch-id", "b1", "--require-stable-batches", "1"]),
                1,
            )
            self.assertEqual(
                main([str(d), "--history", str(history), "--batch-id", "b2", "--require-stable-batches", "1"]),
                0,
            )


if __name__ == "__main__":
    unittest.main()
