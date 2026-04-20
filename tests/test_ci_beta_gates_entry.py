import importlib.util
import json
from pathlib import Path


def _load_beta_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "ci-beta-gates.py"
    spec = importlib.util.spec_from_file_location("ci_beta_gates", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load ci-beta-gates.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_beta_gates_default():
    module = _load_beta_module()
    gates = module.build_beta_gates(include_full_electron=False)
    names = [gate.name for gate in gates]
    assert "typecheck" in names
    assert "factory-backend" in names
    assert "factory-smoke" in names
    assert "electron-full" not in names


def test_build_beta_gates_full_electron():
    module = _load_beta_module()
    gates = module.build_beta_gates(include_full_electron=True)
    names = [gate.name for gate in gates]
    assert "electron-full" in names


def test_main_dry_run_writes_report(tmp_path):
    module = _load_beta_module()
    output_path = tmp_path / "beta-gates.json"
    code = module.main(["--workspace", str(tmp_path), "--output", str(output_path), "--dry-run"])
    assert code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["dry_run"] is True
    assert payload["gates"]


def test_powershell_wrapper_exists():
    repo_root = Path(__file__).resolve().parents[1]
    wrapper = repo_root / "scripts" / "ci-beta-gates.ps1"
    assert wrapper.is_file()
    text = wrapper.read_text(encoding="utf-8")
    assert "ci-beta-gates.py" in text
