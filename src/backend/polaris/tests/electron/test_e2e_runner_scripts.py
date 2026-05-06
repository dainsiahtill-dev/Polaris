import json
import os
import shutil
import subprocess
from pathlib import Path

PANEL_TASK_SPEC = "src/backend/polaris/tests/electron/panel-task.spec.ts"
REAL_FLOW_SPEC = "src/backend/polaris/tests/electron/pm-director-real-flow.spec.ts"
FULL_CHAIN_AUDIT_SPEC = "src/backend/polaris/tests/electron/full-chain-audit.spec.ts"
ACCEPTANCE_RUNNER = "infrastructure/scripts/run-electron-acceptance-e2e.mjs"


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
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    result = subprocess.run(
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
    assert result.returncode == 0, (
        f"node {' '.join(args)} failed with {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    return result


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
