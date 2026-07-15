"""R6-E regression gates: stable tests for common Agent pitfalls.

Covers:
- fake npm lifecycle scripts (echo-only, node -e console.log)
- manifest-only test scripts (package.json validation, not real tests)
- timeout is NOT success (CLI timed out must be failure)
- fail_closed evidence (missing audit/gate = fail)
- thinking-only Director output (must have materialized changes)
- audit diagnostics wrapper (extract_* functions handle missing/empty)

These gates prevent R5/R6 regressions from re-entering the pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.cells.factory.pipeline.internal.bench_gates import (
    _is_fake_npm_lifecycle_script,
    _is_npm_test_script_manifest_only,
    _is_npm_test_script_placeholder,
    build_real_run_gate,
)

# ---------------------------------------------------------------------------
# Gate 1: fake npm lifecycle detection
# ---------------------------------------------------------------------------


class TestFakeNpmLifecycleDetection:
    """Ensure echo-only / node -e console.log scripts are caught."""

    def test_echo_build_completed_is_fake(self) -> None:
        assert _is_fake_npm_lifecycle_script('echo "Build completed"') is True

    def test_echo_starting_application_is_fake(self) -> None:
        assert _is_fake_npm_lifecycle_script('echo "Starting application"') is True

    def test_echo_tests_passed_is_fake(self) -> None:
        assert _is_fake_npm_lifecycle_script('echo "Tests passed"') is True

    def test_node_e_console_log_is_fake(self) -> None:
        assert _is_fake_npm_lifecycle_script("node -e \"console.log('ok')\"") is True

    def test_bun_e_print_is_fake(self) -> None:
        assert _is_fake_npm_lifecycle_script("bun -e \"print('ok')\"") is True

    def test_npx_tsx_e_is_fake(self) -> None:
        assert _is_fake_npm_lifecycle_script("npx tsx -e \"console.log('ok')\"") is True

    def test_real_tsc_is_not_fake(self) -> None:
        assert _is_fake_npm_lifecycle_script("tsc") is False

    def test_real_jest_is_not_fake(self) -> None:
        assert _is_fake_npm_lifecycle_script("jest") is False

    def test_real_npm_run_build_is_not_fake(self) -> None:
        assert _is_fake_npm_lifecycle_script("npm run build") is False

    def test_echo_with_chained_command_is_not_fake(self) -> None:
        # echo && npm test is a real command chain
        assert _is_fake_npm_lifecycle_script("echo done && npm test") is False

    def test_echo_with_pipe_is_not_fake(self) -> None:
        assert _is_fake_npm_lifecycle_script("echo done | tee log") is False

    def test_node_e_with_real_logic_is_not_fake(self) -> None:
        assert _is_fake_npm_lifecycle_script("node -e \"require('./app').start()\"") is False

    def test_empty_string_is_not_fake(self) -> None:
        assert _is_fake_npm_lifecycle_script("") is False

    def test_none_is_not_fake(self) -> None:
        assert _is_fake_npm_lifecycle_script(None) is False


# ---------------------------------------------------------------------------
# Gate 2: manifest-only test script detection
# ---------------------------------------------------------------------------


class TestManifestOnlyTestDetection:
    """Ensure test scripts that only validate package.json are caught."""

    def test_manifest_check_passed_is_manifest_only(self) -> None:
        cmd = "node -e \"console.log('manifest check passed')\""
        assert _is_npm_test_script_manifest_only(cmd) is True

    def test_read_package_json_is_manifest_only(self) -> None:
        cmd = "node -e \"const fs=require('fs');JSON.parse(fs.readFileSync('package.json','utf8'));console.log('ok')\""
        assert _is_npm_test_script_manifest_only(cmd) is True

    def test_exists_sync_dist_is_manifest_only(self) -> None:
        cmd = "node -e \"if(!require('fs').existsSync('dist/'))throw 1\""
        assert _is_npm_test_script_manifest_only(cmd) is True

    def test_tsconfig_validation_is_manifest_only(self) -> None:
        cmd = "node -e \"if(!JSON.parse(require('fs').readFileSync('tsconfig.json')).compilerOptions)throw 1\""
        assert _is_npm_test_script_manifest_only(cmd) is True

    def test_real_jest_is_not_manifest_only(self) -> None:
        assert _is_npm_test_script_manifest_only("jest") is False

    def test_real_mocha_is_not_manifest_only(self) -> None:
        assert _is_npm_test_script_manifest_only("mocha --recursive") is False

    def test_node_test_file_is_not_manifest_only(self) -> None:
        assert _is_npm_test_script_manifest_only("node tests/test-main.js") is False

    def test_empty_is_not_manifest_only(self) -> None:
        assert _is_npm_test_script_manifest_only("") is False


class TestPlaceholderTestDetection:
    """Ensure test scripts with placeholder messages are caught."""

    def test_no_tests_specified_is_placeholder(self) -> None:
        assert _is_npm_test_script_placeholder('echo "No tests specified"') is True

    def test_all_tests_passed_is_placeholder(self) -> None:
        assert _is_npm_test_script_placeholder('echo "All tests passed"') is True

    def test_tests_not_implemented_is_placeholder(self) -> None:
        assert _is_npm_test_script_placeholder('echo "Tests not implemented"') is True

    def test_no_tests_yet_is_placeholder(self) -> None:
        assert _is_npm_test_script_placeholder('echo "No tests yet"') is True

    def test_real_command_is_not_placeholder(self) -> None:
        assert _is_npm_test_script_placeholder("jest --coverage") is False


# ---------------------------------------------------------------------------
# Gate 3: timeout is NOT success
# ---------------------------------------------------------------------------


class TestTimeoutIsNotSuccess:
    """CLI timeout must be marked as failure, not success."""

    def test_python_cli_timeout_is_failure(self, tmp_path: Path) -> None:
        """Timeout is NOT success - even if the server started."""
        (tmp_path / "app.py").write_text(
            "import time\nprint('Server starting...')\ntime.sleep(60)\n",
            encoding="utf-8",
        )
        record = {"code_files": ["app.py"]}

        gate = build_real_run_gate(tmp_path, record, timeout_s=2)

        assert gate["ok"] is False
        assert gate["requirements"]["entrypoint_smoke"]["ok"] is False
        assert gate["entrypoint"]["started"] is True
        assert gate["entrypoint"]["timeout"] is True
        assert gate["entrypoint"]["detail"] == "CLI timed out - not considered successful"

    def test_npm_start_timeout_is_failure(self, monkeypatch: Any, tmp_path: Path) -> None:
        """npm start timeout must not be treated as success."""
        from polaris.cells.factory.pipeline.internal import bench_gates

        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"start": "node server.js"}}),
            encoding="utf-8",
        )
        (tmp_path / "server.js").write_text("console.log('ok');\n", encoding="utf-8")

        def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
            if command == ["npm", "run", "start"]:
                return {
                    "command": command,
                    "ok": False,
                    "returncode": None,
                    "duration_s": 3.0,
                    "stdout_tail": "Server started on port 3000",
                    "stderr_tail": "",
                    "timeout": True,
                }
            return {
                "command": command,
                "ok": True,
                "returncode": 0,
                "duration_s": 0.01,
                "stdout_tail": "",
                "stderr_tail": "",
                "timeout": False,
            }

        monkeypatch.setattr(bench_gates.shutil, "which", lambda name: "/tool/npm" if name == "npm" else None)
        monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
        record = {"code_files": ["server.js"]}

        gate = build_real_run_gate(tmp_path, record, timeout_s=10)

        assert gate["ok"] is False
        assert gate["entrypoint"]["ok"] is False


# ---------------------------------------------------------------------------
# Gate 4: fail_closed evidence
# ---------------------------------------------------------------------------


class TestFailClosedEvidence:
    """Missing audit/gate must be fail-closed, not silently passing."""

    def test_missing_real_run_gate_is_fail_closed(self) -> None:
        from polaris.kernelone.benchmark.audit_diagnostics import extract_real_run_diagnostics

        diag = extract_real_run_diagnostics({})
        assert diag["has_gate"] is False
        assert diag["ok"] is False

    def test_missing_llm_route_audit_is_fail_closed(self) -> None:
        from polaris.kernelone.benchmark.audit_diagnostics import extract_director_route_diagnostics

        diag = extract_director_route_diagnostics({})
        assert diag["has_audit"] is False
        assert diag["ok"] is False

    def test_none_llm_route_audit_is_fail_closed(self) -> None:
        from polaris.kernelone.benchmark.audit_diagnostics import extract_director_route_diagnostics

        diag = extract_director_route_diagnostics({"llm_route_audit": None})
        assert diag["has_audit"] is False
        assert diag["ok"] is False

    def test_missing_failure_taxonomy_for_failed_record_is_fail_closed(self) -> None:
        from polaris.kernelone.benchmark.audit_diagnostics import extract_failure_taxonomy

        diag = extract_failure_taxonomy({"all_checks_passed": False})
        assert diag["has_taxonomy"] is False
        assert diag["ok"] is False
        assert diag["root_cause_signature"] == "unclassified"

    def test_empty_stage_failure_is_fail_closed(self) -> None:
        from polaris.kernelone.benchmark.audit_diagnostics import extract_stage_failure

        diag = extract_stage_failure({})
        assert diag["chain_state"] == ""
        assert diag["qa_ran"] is None
        assert diag["gate_failures"] == []
        assert diag["check_failures"] == []

    def test_qa_empty_record_is_fail_closed(self) -> None:
        from polaris.kernelone.benchmark.audit_diagnostics import extract_qa_diagnostics

        diag = extract_qa_diagnostics({})
        assert diag["has_plan_doc"] is False
        assert diag["has_qa_verdict"] is False
        assert diag["wrong_product_suspect"] is False


# ---------------------------------------------------------------------------
# Gate 5: thinking-only Director output
# ---------------------------------------------------------------------------


class TestThinkingOnlyDirectorOutput:
    """Director output must have materialized changes, not just thinking."""

    def test_normalize_director_role_response_extracts_content(self) -> None:
        from polaris.cells.roles.adapters.internal.director.adapter import _normalize_director_role_response

        raw = {
            "content": "I will create src/app.py with a main function.",
            "tool_results": [{"tool": "write_file", "success": True, "result": {"path": "src/app.py"}}],
        }
        normalized = _normalize_director_role_response(raw)
        assert normalized["content"] == "I will create src/app.py with a main function."
        assert len(normalized["tool_results"]) == 1

    def test_normalize_director_role_response_handles_missing_content(self) -> None:
        from polaris.cells.roles.adapters.internal.director.adapter import _normalize_director_role_response

        raw: Any = {"tool_results": []}
        normalized = _normalize_director_role_response(raw)
        assert normalized["content"] == ""

    def test_normalize_director_role_response_handles_string_response(self) -> None:
        from polaris.cells.roles.adapters.internal.director.adapter import _normalize_director_role_response

        raw: Any = "plain text response"
        normalized = _normalize_director_role_response(raw)
        assert normalized["content"] == "plain text response"
        assert normalized["tool_results"] == []


# ---------------------------------------------------------------------------
# Gate 6: audit diagnostics wrapper robustness
# ---------------------------------------------------------------------------


class TestAuditDiagnosticsWrapper:
    """Extract functions must handle missing/empty/malformed data gracefully."""

    def test_diagnose_project_minimal_record(self) -> None:
        from polaris.kernelone.benchmark.audit_diagnostics import diagnose_project

        record: dict[str, Any] = {
            "project_id": "L5-30",
            "level": 5,
            "all_checks_passed": True,
            "code_file_count": 10,
        }
        diag = diagnose_project(record)
        assert diag["project_id"] == "L5-30"
        assert diag["all_checks_passed"] is True
        assert diag["director_route"]["has_audit"] is False
        assert diag["real_run"]["has_gate"] is False

    def test_diagnose_run_empty(self) -> None:
        from polaris.kernelone.benchmark.audit_diagnostics import diagnose_run

        report = diagnose_run({}, [])
        assert report["run_summary"]["total"] == 0
        assert report["run_summary"]["passed"] == 0
        assert report["projects"] == []

    def test_load_per_project_audits_missing_dir(self, tmp_path: Path) -> None:
        from polaris.kernelone.benchmark.audit_diagnostics import load_per_project_audits

        records = load_per_project_audits(tmp_path / "nonexistent")
        assert records == []

    def test_load_per_project_audits_skips_malformed_json(self, tmp_path: Path) -> None:
        from polaris.kernelone.benchmark.audit_diagnostics import load_per_project_audits

        run_dir = tmp_path / "audits" / "run-bad"
        run_dir.mkdir(parents=True)
        (run_dir / "good.audit.json").write_text(
            json.dumps({"project_id": "good"}),
            encoding="utf-8",
        )
        (run_dir / "bad.audit.json").write_text("NOT JSON{{{", encoding="utf-8")

        records = load_per_project_audits(run_dir)
        assert len(records) == 1
        assert records[0]["project_id"] == "good"

    def test_load_factory_audits_json_missing_file(self, tmp_path: Path) -> None:
        from polaris.kernelone.benchmark.audit_diagnostics import load_factory_audits_json

        data = load_factory_audits_json(tmp_path / "nonexistent.json")
        assert data == {}

    def test_diagnose_from_paths_missing_both(self, tmp_path: Path) -> None:
        from polaris.kernelone.benchmark.audit_diagnostics import diagnose_from_paths

        report = diagnose_from_paths(None, None)
        assert report["run_summary"]["total"] == 0
        assert report["projects"] == []

    def test_diagnose_from_paths_nonexistent_paths(self, tmp_path: Path) -> None:
        from polaris.kernelone.benchmark.audit_diagnostics import diagnose_from_paths

        report = diagnose_from_paths(
            tmp_path / "no_such_file.json",
            tmp_path / "no_such_dir",
        )
        assert report["run_summary"]["total"] == 0


# ---------------------------------------------------------------------------
# Gate 7: bench_gates fail_closed for factory_run_service
# ---------------------------------------------------------------------------


class TestFactoryRunServiceFailClosed:
    """Factory run service gates must be fail-closed."""

    def test_missing_qa_verdict_is_fail_closed(self) -> None:
        from scripts.factory_bench.run_factory_bench import apply_factory_bench_gates

        record: dict[str, Any] = {
            "all_checks_passed": True,
            "has_plan_doc": True,
            "has_blueprint_doc": True,
            "has_qa_verdict": False,
            "chain_state": "clean",
            "chain_results": {"qa_ran": True, "qa_passed": True},
            "wrong_product_suspect": False,
        }
        apply_factory_bench_gates(record, chain={"exit_code": 0})
        assert record["all_checks_passed"] is False
        gates = {gate["gate"]: gate for gate in record["factory_gates"]}
        assert "qa_verdict_artifact_present" not in gates
        assert gates["canonical_execution"]["ok"] is False

    def test_wrong_product_suspect_is_fail_closed(self) -> None:
        from scripts.factory_bench.run_factory_bench import apply_factory_bench_gates

        record: dict[str, Any] = {
            "all_checks_passed": True,
            "has_plan_doc": True,
            "has_blueprint_doc": True,
            "has_qa_verdict": True,
            "chain_state": "clean",
            "chain_results": {"qa_ran": True, "qa_passed": True},
            "wrong_product_suspect": True,
        }
        apply_factory_bench_gates(record, chain={"exit_code": 0})
        assert record["all_checks_passed"] is False
        gates = {gate["gate"]: gate for gate in record["factory_gates"]}
        assert gates["wrong_product_guard"]["ok"] is False

    def test_legacy_chain_failure_cannot_replace_missing_canonical_projection(self) -> None:
        from scripts.factory_bench.run_factory_bench import apply_factory_bench_gates

        record: dict[str, Any] = {
            "all_checks_passed": True,
            "has_plan_doc": True,
            "has_blueprint_doc": True,
            "has_qa_verdict": True,
            "chain_state": "fail",
            "chain_results": {"qa_ran": False, "qa_passed": False},
            "wrong_product_suspect": False,
        }
        apply_factory_bench_gates(record, chain={"exit_code": 1})
        assert record["static_checks_passed"] is True
        assert record["all_checks_passed"] is False
        gates = {gate["gate"]: gate for gate in record["factory_gates"]}
        assert "chain_clean" not in gates
        assert gates["canonical_execution"]["ok"] is False

    def test_clean_chain_preserves_static_pass(self) -> None:
        from scripts.factory_bench.run_factory_bench import apply_factory_bench_gates

        record: dict[str, Any] = {
            "all_checks_passed": True,
            "has_plan_doc": True,
            "has_blueprint_doc": True,
            "has_qa_verdict": True,
            "chain_state": "clean",
            "chain_results": {"qa_ran": True, "qa_passed": True},
            "wrong_product_suspect": False,
            "implementation_depth": {"ok": True, "detail": "implementation depth passed"},
            "real_run_gate": {"ok": True, "summary": "real run gate passed"},
            "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
            "backend_freshness": {"ok": True, "detail": "backend fingerprint matches source"},
            "run_ledger": {
                "ledger_path": __file__,
                "content_id": "content-id",
                "event_id": "content-id",
                "append_id": "append-id",
                "job_token_id": "job-token-id",
                "job_token": {"capability_audit": {"ok": True, "issues": []}},
            },
            "run_ledger_projection": {
                "source": "run_ledger",
                "integrity_ok": True,
                "outcome_ok": True,
                "ok": True,
                "event_count": 1,
                "gate_count": 1,
                "failed_gates": [],
                "capability": {"ok": True, "issues": [], "latest_token_id": "job-token-id"},
                "physical_evidence": {},
            },
            "canonical_projection": {
                "source": "canonical_projection",
                "execution": {
                    "ok": True,
                    "reason_code": "completed_verified",
                }
            },
        }
        apply_factory_bench_gates(record, chain={"exit_code": 0})
        assert record["static_checks_passed"] is True
        assert record["all_checks_passed"] is True, record["factory_gates"]
        assert all(gate["ok"] for gate in record["factory_gates"])

    def test_fail_closed_count_not_counted_as_evidence(self) -> None:
        """fail_closed diagnostic events must NOT be counted as real director evidence.

        This is a structural test verifying the fail_closed diagnostic separation
        in the factory pipeline. The _build_fail_closed_director_route_events method
        creates diagnostic events with fail_closed=True that are explicitly excluded
        from real run evidence counting.
        """
        import inspect

        from polaris.cells.factory.pipeline.internal.factory_stage_executor import (
            OrchestrationStageExecutor,
        )

        # Verify the class has the fail_closed diagnostic builder
        assert hasattr(OrchestrationStageExecutor, "_build_fail_closed_director_route_events")

        # Verify the fail_closed events are marked with fail_closed=True
        # This ensures they cannot be counted as real evidence
        source = inspect.getsource(OrchestrationStageExecutor._build_fail_closed_director_route_events)
        assert "fail_closed" in source
        assert "no_dispatch_evidence_for_binding" in source
