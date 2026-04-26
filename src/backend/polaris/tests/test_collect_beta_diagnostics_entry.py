import importlib.util
import json
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "collect_beta_diagnostics.py"
    spec = importlib.util.spec_from_file_location("collect_beta_diagnostics", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load collect_beta_diagnostics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_diagnostics_reads_reports(tmp_path):
    module = _load_module()
    reports_dir = tmp_path / ".polaris" / "reports"
    reports_dir.mkdir(parents=True)

    # Test: reaudit-beta-gates should be selected over local-beta-gates when newer
    # First create older local-beta-gates
    local_gate = reports_dir / "local-beta-gates.json"
    local_gate.write_text(
        json.dumps({
            "status": "PASS",
            "generated_at": "2026-03-06T00:00:00Z",
            "gates": [{"name": "build", "status": "PASS", "log_path": ".polaris/reports/beta-gates-logs/02-build.log"}],
        }),
        encoding="utf-8",
    )

    # Create newer reaudit-beta-gates
    reaudit_gate = reports_dir / "reaudit-beta-gates.json"
    reaudit_gate.write_text(
        json.dumps({
            "status": "FAIL",
            "generated_at": "2026-03-07T00:00:00Z",
            "gates": [{"name": "build", "status": "FAIL", "log_path": ".polaris/reports/beta-gates-logs/01-build.log"}],
        }),
        encoding="utf-8",
    )

    # Wait briefly and touch to ensure different mtime
    import time
    time.sleep(0.1)
    reaudit_gate.write_bytes(reaudit_gate.read_bytes())

    (reports_dir / "local-factory-smoke.json").write_text(
        json.dumps({"status": "PASS", "results": []}),
        encoding="utf-8",
    )
    log_dir = reports_dir / "beta-gates-logs"
    log_dir.mkdir()
    (log_dir / "01-build.log").write_text("failed", encoding="utf-8")
    (log_dir / "02-build.log").write_text("ok", encoding="utf-8")
    trace_dir = tmp_path / "test-results" / "sample"
    trace_dir.mkdir(parents=True)
    (trace_dir / "trace.zip").write_text("trace", encoding="utf-8")

    payload = module.build_diagnostics(tmp_path)
    # Should pick the newer reaudit-beta-gates report with FAIL status
    assert payload["status"] == "FAIL"
    assert payload["reports"]["beta_gates"]["status"] == "FAIL"
    assert "reaudit-beta-gates" in payload["reports"]["beta_gates"]["path"]
    assert payload["reports"]["factory_smoke"]["status"] == "PASS"
    assert payload["evidence_paths"]["logs"]
    assert payload["evidence_paths"]["traces"]


def test_build_diagnostics_handles_multiple_prefixes(tmp_path):
    """Test that _find_latest_beta_report correctly selects the most recent by mtime."""
    module = _load_module()
    reports_dir = tmp_path / ".polaris" / "reports"
    reports_dir.mkdir(parents=True)

    # Create files with different prefixes and timestamps
    # Oldest
    old = reports_dir / "beta-gates-old.json"
    old.write_text(json.dumps({"status": "OLD", "gates": []}), encoding="utf-8")
    import time
    time.sleep(0.05)

    # Middle
    middle = reports_dir / "ci-beta-gates.json"
    middle.write_text(json.dumps({"status": "MIDDLE", "gates": []}), encoding="utf-8")
    time.sleep(0.05)

    # Newest
    newest = reports_dir / "manual-beta-gates.json"
    newest.write_text(json.dumps({"status": "NEWEST", "gates": []}), encoding="utf-8")

    payload = module.build_diagnostics(tmp_path)
    # Should pick manual-beta-gates as it's the newest by mtime
    assert payload["reports"]["beta_gates"]["status"] == "NEWEST"
    assert "manual-beta-gates" in payload["reports"]["beta_gates"]["path"]


def test_main_writes_output(tmp_path):
    module = _load_module()
    reports_dir = tmp_path / ".polaris" / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "local-beta-gates.json").write_text(json.dumps({"status": "FAIL", "gates": []}), encoding="utf-8")
    output_path = tmp_path / "diagnostics.json"
    code = module.main(["--workspace", str(tmp_path), "--output", str(output_path)])
    assert code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["reports"]["beta_gates"]["status"] == "FAIL"
