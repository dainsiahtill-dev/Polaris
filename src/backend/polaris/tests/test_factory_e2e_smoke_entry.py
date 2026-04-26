import json
import importlib.util
from pathlib import Path


def _load_smoke_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "run_factory_e2e_smoke.py"
    spec = importlib.util.spec_from_file_location("run_factory_e2e_smoke", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load run_factory_e2e_smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_commands_default():
    module = _load_smoke_module()
    commands = module.build_commands(full=False)
    assert commands and isinstance(commands, list)
    flattened = " ".join(commands[0])
    assert "src/backend/tests/test_factory_run_service.py" in flattened
    assert "src/backend/tests/test_factory_router.py" in flattened
    assert "src/backend/tests/test_factory_contract_snapshot.py" in flattened
    assert "tests/functional/test_pm_loop.py" in flattened
    assert "tests/functional/test_director_flow.py" in flattened
    assert "tests/test_factory_e2e_smoke_entry.py" in flattened
    assert "src/backend/tests/test_history_factory_overview.py" in flattened


def test_main_dry_run_returns_zero(tmp_path):
    module = _load_smoke_module()
    code = module.main(["--workspace", str(tmp_path), "--dry-run"])
    assert code == 0


def test_build_commands_full_includes_frontend_checks():
    module = _load_smoke_module()
    commands = module.build_commands(full=True)
    npm_cmd = module._npm_executable()
    npx_cmd = module._npx_executable()
    assert len(commands) >= 4
    assert any(cmd[:2] == [npx_cmd, "vitest"] for cmd in commands)
    assert any(cmd[:3] == [npm_cmd, "run", "test:e2e:panel"] for cmd in commands)
    assert any(cmd[:3] == [npm_cmd, "run", "build"] for cmd in commands)


def test_main_dry_run_writes_report(tmp_path):
    module = _load_smoke_module()
    output_path = tmp_path / "factory-e2e-smoke.json"
    code = module.main(["--workspace", str(tmp_path), "--output", str(output_path), "--dry-run"])
    assert code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert payload["results"]


def test_powershell_wrapper_exists():
    repo_root = Path(__file__).resolve().parents[1]
    wrapper = repo_root / "scripts" / "run_factory_e2e_smoke.ps1"
    assert wrapper.is_file()
    text = wrapper.read_text(encoding="utf-8")
    assert "run_factory_e2e_smoke.py" in text
