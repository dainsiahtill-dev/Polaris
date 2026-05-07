import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PANEL_TASK_SPEC = "src/backend/polaris/tests/electron/panel-task.spec.ts"
REAL_FLOW_SPEC = "src/backend/polaris/tests/electron/pm-director-real-flow.spec.ts"
FULL_CHAIN_AUDIT_SPEC = "src/backend/polaris/tests/electron/full-chain-audit.spec.ts"
ACCEPTANCE_RUNNER = "infrastructure/scripts/run-electron-acceptance-e2e.mjs"
REAL_FLOW_RUNNER = "infrastructure/scripts/run-electron-real-flow-e2e.mjs"
BACKEND_PYTEST_SHARD_RUNNER = "infrastructure/scripts/run-backend-pytest-shard.py"


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "package.json").is_file() and (parent / "infrastructure").is_dir():
            return parent
    raise AssertionError("Failed to locate repository root")


REPO_ROOT = _repo_root()


def _node_executable() -> str:
    node = shutil.which("node")
    if node is None:
        raise AssertionError("node executable not found")
    return node


def _run_node(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = _run_node_raw(args, env=env)
    assert result.returncode == 0, (
        f"node {' '.join(args)} failed with {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    return result


def _run_node_raw(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        [_node_executable(), *args],
        cwd=REPO_ROOT,
        env=run_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def test_electron_runner_spec_paths_exist() -> None:
    for relative_path in [PANEL_TASK_SPEC, REAL_FLOW_SPEC, FULL_CHAIN_AUDIT_SPEC]:
        assert (REPO_ROOT / relative_path).is_file(), f"Missing Electron runner spec: {relative_path}"


def test_panel_task_runner_dry_run_uses_existing_spec(tmp_path: Path) -> None:
    task_path = tmp_path / "panel-task.json"
    task_path.write_text(
        json.dumps(
            {
                "prompt": "open a diagnostic panel",
                "navigationSteps": [],
                "fieldAction": {"name": "diagnostic noop"},
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )

    result = _run_node(
        [
            "infrastructure/scripts/run-panel-task-e2e.mjs",
            "--dry-run",
            "--no-semantic-fallback",
            "--task-file",
            str(task_path),
        ]
    )

    assert f'"panel_task_spec": "{PANEL_TASK_SPEC}"' in result.stdout
    assert "tests/electron/panel-task.spec.ts" not in result.stdout.replace(PANEL_TASK_SPEC, "")


def test_real_flow_autofix_dry_run_uses_existing_spec(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    _run_node(
        [
            "infrastructure/scripts/auto-fix-real-flow.mjs",
            "--dry-run",
            "--skip-build",
            "--max-attempts",
            "0",
        ],
        env={"KERNELONE_REAL_FLOW_AUTOFIX_LOG_DIR": str(log_dir)},
    )

    audit_files = sorted(log_dir.glob("*.audit.json"))
    assert len(audit_files) == 1
    payload = json.loads(audit_files[0].read_text(encoding="utf-8"))

    assert payload["settings"]["real_flow_spec"] == REAL_FLOW_SPEC
    assert REAL_FLOW_SPEC in payload["preview"]["test_command"]
    serialized = json.dumps(payload, ensure_ascii=True)
    assert "tests/electron/pm-director-real-flow.spec.ts" not in serialized.replace(REAL_FLOW_SPEC, "")


def test_acceptance_runner_dry_run_uses_existing_specs_and_windows_safe_spawn() -> None:
    result = _run_node(
        [
            ACCEPTANCE_RUNNER,
            "--dry-run",
        ],
        env={"KERNELONE_E2E_USE_REAL_SETTINGS": "1"},
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "DRY_RUN"
    assert payload["specs"] == [FULL_CHAIN_AUDIT_SPEC, REAL_FLOW_SPEC]
    assert FULL_CHAIN_AUDIT_SPEC in payload["spawn_args"]
    assert REAL_FLOW_SPEC in payload["spawn_args"]
    if os.name == "nt":
        assert payload["spawn_command"] == "cmd.exe"
        assert payload["spawn_args"][:4] == ["/d", "/s", "/c", "npx.cmd"]
    else:
        assert payload["spawn_command"] == "npx"


def test_real_flow_runner_dry_run_seeds_utf8_settings_and_uses_existing_specs(tmp_path: Path) -> None:
    settings = {
        "workspace": str(tmp_path / "workspace"),
        "llm_provider": "codex_sdk",
        "llm_model": "gpt-5.4",
    }
    settings_seed = base64.b64encode(json.dumps(settings, ensure_ascii=False).encode("utf-8")).decode("ascii")
    home = tmp_path / "home"

    result = _run_node(
        [
            REAL_FLOW_RUNNER,
            "--dry-run",
        ],
        env={
            "KERNELONE_E2E_SETTINGS_JSON_BASE64": settings_seed,
            "KERNELONE_E2E_LLM_CONFIG_JSON_BASE64": "",
            "KERNELONE_E2E_LLM_CONFIG_JSON": "",
            "KERNELONE_E2E_HOME": str(home),
        },
    )

    payload = json.loads(result.stdout)
    settings_path = home / "config" / "settings.json"

    assert payload["status"] == "DRY_RUN"
    assert payload["settings_source"] == "env:KERNELONE_E2E_SETTINGS_JSON_BASE64"
    assert payload["settings_seeded"] is True
    assert payload["llm_config_source"] == "missing"
    assert payload["llm_config_seeded"] is False
    assert payload["specs"] == [FULL_CHAIN_AUDIT_SPEC, REAL_FLOW_SPEC]
    assert FULL_CHAIN_AUDIT_SPEC in payload["spawn_args"]
    assert REAL_FLOW_SPEC in payload["spawn_args"]
    assert json.loads(settings_path.read_text(encoding="utf-8")) == settings
    assert str(settings_path) not in result.stdout
    if os.name == "nt":
        assert payload["spawn_command"] == "cmd.exe"
        assert payload["spawn_args"][:4] == ["/d", "/s", "/c", "npx.cmd"]
    else:
        assert payload["spawn_command"] == "npx"


def test_real_flow_runner_dry_run_seeds_utf8_llm_config_without_printing_path(tmp_path: Path) -> None:
    settings = {
        "workspace": str(tmp_path / "workspace"),
        "llm_provider": "codex_sdk",
        "llm_model": "gpt-5.4",
    }
    llm_config = {
        "schema_version": 2,
        "providers": {
            "codex_sdk": {
                "type": "codex_sdk",
                "name": "Codex SDK",
                "api_key": "secret-value",
            }
        },
        "roles": {
            "pm": {"provider_id": "codex_sdk", "model": "gpt-5.4", "profile": "pm-default"},
        },
    }
    settings_seed = base64.b64encode(json.dumps(settings, ensure_ascii=False).encode("utf-8")).decode("ascii")
    llm_seed = base64.b64encode(json.dumps(llm_config, ensure_ascii=False).encode("utf-8")).decode("ascii")
    home = tmp_path / "home"

    result = _run_node(
        [
            REAL_FLOW_RUNNER,
            "--dry-run",
        ],
        env={
            "KERNELONE_E2E_SETTINGS_JSON_BASE64": settings_seed,
            "KERNELONE_E2E_LLM_CONFIG_JSON_BASE64": llm_seed,
            "KERNELONE_E2E_HOME": str(home),
        },
    )

    payload = json.loads(result.stdout)
    llm_config_path = home / "config" / "llm" / "llm_config.json"

    assert payload["status"] == "DRY_RUN"
    assert payload["settings_seeded"] is True
    assert payload["llm_config_source"] == "env:KERNELONE_E2E_LLM_CONFIG_JSON_BASE64"
    assert payload["llm_config_seeded"] is True
    assert json.loads(llm_config_path.read_text(encoding="utf-8")) == llm_config
    assert str(llm_config_path) not in result.stdout
    assert "secret-value" not in result.stdout


def test_real_flow_runner_dry_run_seeds_llm_test_index_for_required_roles(tmp_path: Path) -> None:
    settings = {
        "workspace": str(tmp_path / "workspace"),
        "llm_provider": "codex_sdk",
        "llm_model": "gpt-5.4",
    }
    llm_config = {
        "schema_version": 2,
        "providers": {"codex_sdk": {"type": "codex_sdk", "name": "Codex SDK"}},
        "roles": {
            "pm": {"provider_id": "codex_sdk", "model": "gpt-5.4"},
            "director": {"provider_id": "codex_sdk", "model": "gpt-5.4"},
        },
        "policies": {"required_ready_roles": ["pm", "director"]},
    }
    llm_test_index = {
        "version": "2.0",
        "roles": {
            "pm": {"ready": True, "grade": "PASS"},
            "director": {"ready": True, "grade": "PASS"},
        },
        "providers": {},
    }
    settings_seed = base64.b64encode(json.dumps(settings, ensure_ascii=False).encode("utf-8")).decode("ascii")
    llm_seed = base64.b64encode(json.dumps(llm_config, ensure_ascii=False).encode("utf-8")).decode("ascii")
    index_seed = base64.b64encode(json.dumps(llm_test_index, ensure_ascii=False).encode("utf-8")).decode("ascii")
    home = tmp_path / "home"

    result = _run_node(
        [
            REAL_FLOW_RUNNER,
            "--dry-run",
        ],
        env={
            "KERNELONE_E2E_SETTINGS_JSON_BASE64": settings_seed,
            "KERNELONE_E2E_LLM_CONFIG_JSON_BASE64": llm_seed,
            "KERNELONE_E2E_LLM_TEST_INDEX_JSON_BASE64": index_seed,
            "KERNELONE_E2E_HOME": str(home),
        },
    )

    payload = json.loads(result.stdout)
    llm_test_index_path = home / "config" / "llm" / "llm_test_index.json"

    assert payload["status"] == "DRY_RUN"
    assert payload["llm_test_index_source"] == "env:KERNELONE_E2E_LLM_TEST_INDEX_JSON_BASE64"
    assert payload["llm_test_index_seeded"] is True
    assert payload["llm_required_ready_roles"] == ["pm", "director"]
    assert payload["llm_readiness_seed_ok"] is True
    assert payload["llm_readiness_missing_roles"] == []
    assert json.loads(llm_test_index_path.read_text(encoding="utf-8")) == llm_test_index
    assert str(llm_test_index_path) not in result.stdout


def test_real_flow_runner_rejects_missing_llm_test_index_for_required_roles(tmp_path: Path) -> None:
    settings = {
        "workspace": str(tmp_path / "workspace"),
        "llm_provider": "codex_sdk",
        "llm_model": "gpt-5.4",
    }
    llm_config = {
        "schema_version": 2,
        "providers": {"codex_sdk": {"type": "codex_sdk", "name": "Codex SDK"}},
        "roles": {"pm": {"provider_id": "codex_sdk", "model": "gpt-5.4"}},
        "policies": {"required_ready_roles": ["pm"]},
    }
    settings_seed = base64.b64encode(json.dumps(settings, ensure_ascii=False).encode("utf-8")).decode("ascii")
    llm_seed = base64.b64encode(json.dumps(llm_config, ensure_ascii=False).encode("utf-8")).decode("ascii")
    home = tmp_path / "home"
    isolated_host_home = tmp_path / "host-home"

    result = _run_node_raw(
        [
            REAL_FLOW_RUNNER,
        ],
        env={
            "KERNELONE_E2E_SETTINGS_JSON_BASE64": settings_seed,
            "KERNELONE_E2E_LLM_CONFIG_JSON_BASE64": llm_seed,
            "KERNELONE_E2E_LLM_TEST_INDEX_JSON_BASE64": "",
            "KERNELONE_E2E_LLM_TEST_INDEX_JSON": "",
            "KERNELONE_E2E_LLM_TEST_INDEX_PATH": "",
            "KERNELONE_E2E_LLM_TEST_INDEX_HOST_FALLBACK": "0",
            "KERNELONE_E2E_HOME": str(home),
            "KERNELONE_HOME": str(isolated_host_home),
        },
    )

    assert result.returncode == 2
    assert "invalid LLM readiness seed" in result.stderr
    assert "required ready roles" in result.stderr
    assert str(home) not in result.stderr


def test_real_flow_runner_dry_run_reports_missing_settings_without_silent_skip() -> None:
    result = _run_node(
        [
            REAL_FLOW_RUNNER,
            "--dry-run",
        ],
        env={
            "KERNELONE_E2E_SETTINGS_JSON_BASE64": "",
            "KERNELONE_E2E_SETTINGS_JSON": "",
            "KERNELONE_E2E_LLM_CONFIG_JSON_BASE64": "",
            "KERNELONE_E2E_LLM_CONFIG_JSON": "",
            "KERNELONE_HOME": "",
            "KERNELONE_E2E_ALLOW_HOST_SETTINGS": "",
            "CI": "",
            "GITHUB_ACTIONS": "",
        },
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "DRY_RUN"
    assert payload["settings_source"] == "missing"
    assert payload["settings_seeded"] is False
    assert payload["llm_config_source"] == "missing"
    assert payload["llm_config_seeded"] is False
    assert payload["specs"] == [FULL_CHAIN_AUDIT_SPEC, REAL_FLOW_SPEC]


def test_real_flow_runner_dry_run_uses_existing_e2e_home_settings(tmp_path: Path) -> None:
    home = tmp_path / "e2e-home"
    settings_path = home / "config" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"workspace": str(tmp_path / "workspace")}, ensure_ascii=True),
        encoding="utf-8",
    )

    result = _run_node(
        [
            REAL_FLOW_RUNNER,
            "--dry-run",
        ],
        env={
            "KERNELONE_E2E_SETTINGS_JSON_BASE64": "",
            "KERNELONE_E2E_SETTINGS_JSON": "",
            "KERNELONE_HOME": "",
            "KERNELONE_E2E_HOME": str(home),
        },
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "DRY_RUN"
    assert payload["settings_source"] == "env:KERNELONE_E2E_HOME"
    assert payload["runtime_root"].endswith("runtime-cache")
    assert str(settings_path) not in result.stdout


def test_real_flow_runner_rejects_ci_host_settings_fallback(tmp_path: Path) -> None:
    host_home = tmp_path / "host-home"
    settings_path = host_home / "config" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"workspace": str(tmp_path / "workspace")}, ensure_ascii=True),
        encoding="utf-8",
    )

    result = _run_node_raw(
        [
            REAL_FLOW_RUNNER,
            "--dry-run",
        ],
        env={
            "KERNELONE_E2E_SETTINGS_JSON_BASE64": "",
            "KERNELONE_E2E_SETTINGS_JSON": "",
            "KERNELONE_E2E_LLM_CONFIG_JSON_BASE64": "",
            "KERNELONE_E2E_LLM_CONFIG_JSON": "",
            "KERNELONE_HOME": str(host_home),
            "KERNELONE_E2E_ALLOW_HOST_SETTINGS": "1",
            "CI": "true",
            "GITHUB_ACTIONS": "true",
        },
    )

    assert result.returncode == 2
    assert "host settings fallback is not allowed" in result.stderr
    assert str(settings_path) not in result.stderr


def test_backend_pytest_shard_runner_dry_run_balances_collected_tests(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    result = subprocess.run(
        [
            sys.executable,
            BACKEND_PYTEST_SHARD_RUNNER,
            "--dry-run",
            "--tests-root",
            "polaris/tests/electron/test_e2e_runner_scripts.py",
            "--shard-index",
            "1",
            "--shard-count",
            "2",
            "--summary-path",
            str(summary_path),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src" / "backend")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    persisted = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["status"] == "DRY_RUN"
    assert payload["shard_index"] == 1
    assert payload["shard_count"] == 2
    assert payload["total_tests"] >= 1
    assert payload["selected_files"] == 1
    assert payload["files"] == ["polaris/tests/electron/test_e2e_runner_scripts.py"]
    assert persisted == payload


def test_backend_pytest_shard_runner_rejects_invalid_shard_index() -> None:
    result = subprocess.run(
        [
            sys.executable,
            BACKEND_PYTEST_SHARD_RUNNER,
            "--dry-run",
            "--tests-root",
            "polaris/tests/electron/test_e2e_runner_scripts.py",
            "--shard-index",
            "3",
            "--shard-count",
            "2",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src" / "backend")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )

    assert result.returncode != 0
    assert "--shard-index must be between 1 and --shard-count" in result.stderr
