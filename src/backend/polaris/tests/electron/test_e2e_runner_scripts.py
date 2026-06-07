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
WEB_REAL_FLOW_SPEC = "src/backend/polaris/tests/electron/pm-director-real-flow.web.spec.ts"
ELECTRON_FIXTURES = "src/backend/polaris/tests/electron/fixtures.ts"
ACCEPTANCE_RUNNER = "infrastructure/scripts/run-electron-acceptance-e2e.mjs"
REAL_FLOW_RUNNER = "infrastructure/scripts/run-electron-real-flow-e2e.mjs"
DUAL_ENTRY_FULL_CHAIN_RUNNER = "infrastructure/scripts/run-dual-entry-full-chain-e2e.mjs"
PRODUCTION_STABILITY_RUNNER = "infrastructure/scripts/run-production-stability-validation.mjs"
BACKEND_PYTEST_SHARD_RUNNER = "infrastructure/scripts/run-backend-pytest-shard.py"
CORE_TECH_IDS = [
    "acga_graph_cell_governance",
    "kernelone_agent_os",
    "turn_transaction_kernel_ledger",
    "context_plane_isolation",
    "descriptor_context_verify_packs",
    "strategy_profile_overlay_fingerprint",
    "cognitive_runtime_receipt_handoff",
    "session_continuity_engine",
    "context_catalog_graph_semantic_retrieval",
    "repo_intelligence_localizer",
    "akashic_knowledge_pipeline",
    "tool_normalization_edit_blocks",
    "change_set_validation_rollback",
    "task_market_runtime_projection",
    "cognitive_knowledge_distiller",
    "contextos_attention_phase_budgeting",
]


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "package.json").is_file() and (parent / "infrastructure").is_dir():
            return parent
    raise AssertionError("Failed to locate repository root")


REPO_ROOT = _repo_root()


def _is_relative_to(candidate: Path, base: Path) -> bool:
    try:
        candidate.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


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


def _write_matrix_artifact(
    path: Path,
    *,
    missing: list[str] | None = None,
    require_real_chain: bool = True,
    broken_row_sink: str | None = None,
    receipt_id: str = "receipt-1",
    handoff_id: str = "handoff-1",
    task_count: int = 1,
    linked_pm_task_count: int = 1,
    projection_source_count: int = 1,
    candidate_missing_runtime_ids: list[str] | None = None,
) -> None:
    candidate_missing_runtime_ids = candidate_missing_runtime_ids or []
    sinks = {
        "audit": {"present": True, "evidence": [{"type": "runtime_artifact", "ref": "audit.json"}], "findings": []},
        "receipt": {"present": True, "evidence": [{"type": "api", "ref": "/receipt"}], "findings": []},
        "handoff": {"present": True, "evidence": [{"type": "api", "ref": "/handoff"}], "findings": []},
        "task_projection": {"present": True, "evidence": [{"type": "api", "ref": "/tasks"}], "findings": []},
    }
    rows = [
        {"tech_id": tech_id, "sinks": json.loads(json.dumps(sinks, ensure_ascii=True))} for tech_id in CORE_TECH_IDS
    ]
    if broken_row_sink:
        rows[0]["sinks"][broken_row_sink] = {
            "present": False,
            "evidence": [],
            "findings": [f"{broken_row_sink} evidence missing"],
        }
    payload = {
        "schema": "polaris.e2e.expanded_tech_evidence_matrix.v1",
        "generated_at": "2026-06-07T00:00:00.000Z",
        "workspace": "/tmp/workspace",
        "runtime_root": "/tmp/runtime",
        "require_real_chain": require_real_chain,
        "core_runtime_integrations": {
            "expected_count": 16,
            "actual_count": 16,
            "entrypoints_verified_count": 16,
            "missing_ids": [],
            "unexpected_ids": [],
        },
        "core_runtime_evidence_placement": {
            "schema": "polaris.e2e.core_runtime_evidence_placement.v1",
            "expected_sinks": ["audit", "receipt", "handoff", "task_projection"],
            "rows": rows,
            "missing": missing or [],
            "receipt_id": receipt_id,
            "handoff_id": handoff_id,
            "task_projection": {
                "task_count": task_count,
                "linked_pm_task_count": linked_pm_task_count,
                "projection_source_count": projection_source_count,
            },
        },
        "candidate_runtime_coverage": {
            "schema": "polaris.e2e.expanded_candidate_runtime_coverage.v1",
            "expected_count": 64,
            "runtime_proved_count": 64 - len(candidate_missing_runtime_ids),
            "source_proved_count": 0,
            "gate_declared_count": 0,
            "declared_only_count": 0,
            "runtime_required_count": 64,
            "missing_runtime_ids": candidate_missing_runtime_ids,
            "not_runtime_proved_ids": candidate_missing_runtime_ids,
            "rows": [],
        },
        "expanded_candidates": [],
        "probes": [],
        "summary": {
            "pass": 1,
            "fail": 0,
            "warn": 0,
            "skip": 0,
            "required_fail": 0,
            "candidate_count": 64,
        },
    }
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")


def test_electron_runner_spec_paths_exist() -> None:
    for relative_path in [PANEL_TASK_SPEC, REAL_FLOW_SPEC, FULL_CHAIN_AUDIT_SPEC, WEB_REAL_FLOW_SPEC]:
        assert (REPO_ROOT / relative_path).is_file(), f"Missing Electron runner spec: {relative_path}"


def test_electron_fixtures_do_not_default_persistence_or_workspace_inside_repo() -> None:
    fixtures = (REPO_ROOT / ELECTRON_FIXTURES).read_text(encoding="utf-8")

    assert 'path.join(repoRoot, ".polaris", "tmp")' not in fixtures
    assert "env.KERNELONE_WORKSPACE = repoRoot" not in fixtures
    assert "assertOutsideRepo" in fixtures
    assert 'path.join(os.tmpdir(), "Polaris", "electron-e2e-workspace")' in fixtures


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
    assert not _is_relative_to(Path(payload["runtime_root"]), REPO_ROOT)
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
            "pm": {"ready": True, "grade": "PASS", "provider_id": "codex_sdk", "model": "gpt-5.4"},
            "director": {"ready": True, "grade": "PASS", "provider_id": "codex_sdk", "model": "gpt-5.4"},
        },
        "providers": {
            "codex_sdk": {"ready": True, "grade": "PASS", "model": "gpt-5.4"},
        },
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


def test_real_flow_runner_accepts_separator_variant_model_identity_for_required_roles(tmp_path: Path) -> None:
    settings = {
        "workspace": str(tmp_path / "workspace"),
        "llm_provider": "openai_compat",
        "llm_model": "Qwen3-Max",
    }
    llm_config = {
        "schema_version": 2,
        "providers": {"openai_compat-1": {"type": "openai_compat", "name": "Qwen"}},
        "roles": {
            "pm": {"provider_id": "openai_compat-1", "model": "Qwen3-Max"},
        },
        "policies": {"required_ready_roles": ["pm"]},
    }
    llm_test_index = {
        "version": "2.0",
        "roles": {
            "pm": {
                "ready": True,
                "grade": "PASS",
                "provider_id": "openai_compat-1",
                "model": "qwen3 max",
            },
        },
        "providers": {
            "openai_compat-1": {"ready": True, "grade": "PASS", "model": "provider/qwen3_max", "role": "pm"},
        },
    }
    settings_seed = base64.b64encode(json.dumps(settings, ensure_ascii=False).encode("utf-8")).decode("ascii")
    llm_seed = base64.b64encode(json.dumps(llm_config, ensure_ascii=False).encode("utf-8")).decode("ascii")
    index_seed = base64.b64encode(json.dumps(llm_test_index, ensure_ascii=False).encode("utf-8")).decode("ascii")

    result = _run_node(
        [
            REAL_FLOW_RUNNER,
            "--dry-run",
        ],
        env={
            "KERNELONE_E2E_SETTINGS_JSON_BASE64": settings_seed,
            "KERNELONE_E2E_LLM_CONFIG_JSON_BASE64": llm_seed,
            "KERNELONE_E2E_LLM_TEST_INDEX_JSON_BASE64": index_seed,
            "KERNELONE_E2E_HOME": str(tmp_path / "home"),
        },
    )

    payload = json.loads(result.stdout)

    assert payload["status"] == "DRY_RUN"
    assert payload["llm_readiness_seed_ok"] is True
    assert payload["llm_readiness_binding_issues"] == []


def test_real_flow_runner_accepts_bom_prefixed_real_home_json(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config_dir = home / "config"
    llm_dir = config_dir / "llm"
    llm_dir.mkdir(parents=True)
    settings = {"workspace": str(tmp_path / "workspace")}
    llm_config = {
        "schema_version": 2,
        "providers": {"codex_sdk": {"type": "codex_sdk", "name": "Codex SDK"}},
        "roles": {"pm": {"provider_id": "codex_sdk", "model": "gpt-5.4"}},
        "policies": {"required_ready_roles": ["pm"]},
    }
    llm_test_index = {
        "version": "2.0",
        "roles": {"pm": {"ready": True, "grade": "PASS", "provider_id": "codex_sdk", "model": "gpt-5.4"}},
        "providers": {"codex_sdk": {"ready": True, "grade": "PASS", "model": "gpt-5.4"}},
    }
    (config_dir / "settings.json").write_text("\ufeff" + json.dumps(settings), encoding="utf-8")
    (llm_dir / "llm_config.json").write_text("\ufeff" + json.dumps(llm_config), encoding="utf-8")
    (llm_dir / "llm_test_index.json").write_text("\ufeff" + json.dumps(llm_test_index), encoding="utf-8")

    result = _run_node(
        [
            REAL_FLOW_RUNNER,
            "--dry-run",
        ],
        env={
            "KERNELONE_HOME": str(home),
            "KERNELONE_E2E_USE_REAL_SETTINGS": "1",
        },
    )

    payload = json.loads(result.stdout)

    assert payload["status"] == "DRY_RUN"
    assert payload["settings_source"] == "env:KERNELONE_HOME"
    assert payload["llm_config_seeded"] is True
    assert payload["llm_test_index_seeded"] is True
    assert payload["llm_readiness_seed_ok"] is True


def test_real_flow_runner_rejects_stale_llm_test_index_model_for_required_roles(tmp_path: Path) -> None:
    settings = {
        "workspace": str(tmp_path / "workspace"),
        "llm_provider": "minimax",
        "llm_model": "MiniMax-M2.7-highspeed",
    }
    llm_config = {
        "schema_version": 2,
        "providers": {"minimax-1": {"type": "minimax", "name": "MiniMax"}},
        "roles": {
            "pm": {"provider_id": "minimax-1", "model": "MiniMax-M2.7-highspeed"},
        },
        "policies": {"required_ready_roles": ["pm"]},
    }
    llm_test_index = {
        "version": "2.0",
        "roles": {
            "pm": {"ready": True, "grade": "PASS", "provider_id": "minimax-1", "model": "MiniMax-M2.5"},
        },
        "providers": {
            "minimax-1": {"ready": True, "grade": "PASS", "model": "MiniMax-M2.5", "role": "pm"},
        },
    }
    settings_seed = base64.b64encode(json.dumps(settings, ensure_ascii=False).encode("utf-8")).decode("ascii")
    llm_seed = base64.b64encode(json.dumps(llm_config, ensure_ascii=False).encode("utf-8")).decode("ascii")
    index_seed = base64.b64encode(json.dumps(llm_test_index, ensure_ascii=False).encode("utf-8")).decode("ascii")

    result = _run_node_raw(
        [
            REAL_FLOW_RUNNER,
        ],
        env={
            "KERNELONE_E2E_SETTINGS_JSON_BASE64": settings_seed,
            "KERNELONE_E2E_LLM_CONFIG_JSON_BASE64": llm_seed,
            "KERNELONE_E2E_LLM_TEST_INDEX_JSON_BASE64": index_seed,
            "KERNELONE_E2E_HOME": str(tmp_path / "home"),
        },
    )

    assert result.returncode == 2
    assert "invalid LLM readiness seed" in result.stderr
    assert "model_mismatch" in result.stderr


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


def test_real_flow_runner_rejects_repo_local_e2e_home(tmp_path: Path) -> None:
    settings = {
        "workspace": str(tmp_path / "workspace"),
        "llm_provider": "codex_sdk",
        "llm_model": "gpt-5.4",
    }
    settings_seed = base64.b64encode(json.dumps(settings, ensure_ascii=False).encode("utf-8")).decode("ascii")
    repo_local_home = REPO_ROOT / ".polaris" / "e2e-real-home"

    result = _run_node_raw(
        [
            REAL_FLOW_RUNNER,
            "--dry-run",
        ],
        env={
            "KERNELONE_E2E_SETTINGS_JSON_BASE64": settings_seed,
            "KERNELONE_E2E_HOME": str(repo_local_home),
        },
    )

    assert result.returncode == 2
    assert "must not be inside the Polaris meta-project repository" in result.stderr
    assert str(repo_local_home) not in result.stderr


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
    assert payload["runtime_root"]
    assert not _is_relative_to(Path(payload["runtime_root"]), REPO_ROOT)
    assert not _is_relative_to(Path(payload["runtime_root"]), home)
    assert str(settings_path) not in result.stdout


def test_dual_entry_full_chain_runner_dry_run_uses_desktop_and_web_specs() -> None:
    result = _run_node(
        [
            DUAL_ENTRY_FULL_CHAIN_RUNNER,
            "--dry-run",
        ],
        env={"KERNELONE_E2E_USE_REAL_SETTINGS": "1"},
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "DRY_RUN"
    assert payload["entrypoints"] == ["desktop", "web"]
    assert payload["specs"] == [FULL_CHAIN_AUDIT_SPEC, WEB_REAL_FLOW_SPEC]
    assert FULL_CHAIN_AUDIT_SPEC in payload["spawn_args"]
    assert WEB_REAL_FLOW_SPEC in payload["spawn_args"]
    assert payload["summary_root"].endswith("test-results/electron")
    assert payload["summary_output"].endswith(
        "test-results/electron-dual-full-chain/dual-entry-full-chain-summary.json"
    )
    assert payload["summary_min_mtime_ms"] > 0
    assert payload["child_env"]["KERNELONE_E2E_USE_REAL_SETTINGS"] == "1"
    assert payload["child_env"]["KERNELONE_NATS_ENABLED"] == "0"
    assert payload["child_env"]["KERNELONE_NATS_REQUIRED"] == "0"
    if os.name == "nt":
        assert payload["spawn_command"] == "cmd.exe"
        assert payload["spawn_args"][:4] == ["/d", "/s", "/c", "npx.cmd"]
    else:
        assert payload["spawn_command"] == "npx"


def test_dual_entry_full_chain_runner_dry_run_seeds_real_settings_and_llm_config(tmp_path: Path) -> None:
    settings = {
        "workspace": str(tmp_path / "workspace"),
        "llm_provider": "openai_compat",
        "llm_model": "Qwen3-Max",
    }
    llm_config = {
        "schema_version": 2,
        "providers": {"openai_compat-1": {"type": "openai_compat", "name": "Qwen"}},
        "roles": {
            "pm": {"provider_id": "openai_compat-1", "model": "Qwen3-Max"},
            "director": {"provider_id": "openai_compat-1", "model": "Qwen3-Max"},
        },
        "policies": {"required_ready_roles": ["pm", "director"]},
    }
    llm_test_index = {
        "version": "2.0",
        "roles": {
            "pm": {"ready": True, "grade": "PASS", "provider_id": "openai_compat-1", "model": "qwen3 max"},
            "director": {"ready": True, "grade": "PASS", "provider_id": "openai_compat-1", "model": "Qwen3-Max"},
        },
        "providers": {"openai_compat-1": {"ready": True, "grade": "PASS", "model": "provider/qwen3_max"}},
    }
    settings_seed = base64.b64encode(json.dumps(settings, ensure_ascii=False).encode("utf-8")).decode("ascii")
    llm_seed = base64.b64encode(json.dumps(llm_config, ensure_ascii=False).encode("utf-8")).decode("ascii")
    index_seed = base64.b64encode(json.dumps(llm_test_index, ensure_ascii=False).encode("utf-8")).decode("ascii")
    home = tmp_path / "home"

    result = _run_node(
        [
            DUAL_ENTRY_FULL_CHAIN_RUNNER,
            "--dry-run",
        ],
        env={
            "KERNELONE_E2E_SETTINGS_JSON_BASE64": settings_seed,
            "KERNELONE_E2E_LLM_CONFIG_JSON_BASE64": llm_seed,
            "KERNELONE_E2E_LLM_TEST_INDEX_JSON_BASE64": index_seed,
            "KERNELONE_E2E_HOME": str(home),
            "KERNELONE_E2E_USE_REAL_SETTINGS": "",
        },
    )

    payload = json.loads(result.stdout)
    settings_path = home / "config" / "settings.json"
    llm_config_path = home / "config" / "llm" / "llm_config.json"
    llm_test_index_path = home / "config" / "llm" / "llm_test_index.json"

    assert payload["status"] == "DRY_RUN"
    assert payload["settings_source"] == "env:KERNELONE_E2E_SETTINGS_JSON_BASE64"
    assert payload["settings_seeded"] is True
    assert payload["llm_config_source"] == "env:KERNELONE_E2E_LLM_CONFIG_JSON_BASE64"
    assert payload["llm_config_seeded"] is True
    assert payload["llm_test_index_source"] == "env:KERNELONE_E2E_LLM_TEST_INDEX_JSON_BASE64"
    assert payload["llm_test_index_seeded"] is True
    assert payload["llm_required_ready_roles"] == ["pm", "director"]
    assert payload["llm_readiness_seed_ok"] is True
    assert not _is_relative_to(Path(payload["runtime_root"]), REPO_ROOT)
    assert json.loads(settings_path.read_text(encoding="utf-8")) == settings
    assert json.loads(llm_config_path.read_text(encoding="utf-8")) == llm_config
    assert json.loads(llm_test_index_path.read_text(encoding="utf-8")) == llm_test_index
    assert str(settings_path) not in result.stdout
    assert str(llm_config_path) not in result.stdout
    assert str(llm_test_index_path) not in result.stdout


def test_dual_entry_full_chain_runner_rejects_stale_llm_readiness_seed(tmp_path: Path) -> None:
    settings = {
        "workspace": str(tmp_path / "workspace"),
        "llm_provider": "minimax",
        "llm_model": "MiniMax-M2.7-highspeed",
    }
    llm_config = {
        "schema_version": 2,
        "providers": {"minimax-1": {"type": "minimax", "name": "MiniMax"}},
        "roles": {"pm": {"provider_id": "minimax-1", "model": "MiniMax-M2.7-highspeed"}},
        "policies": {"required_ready_roles": ["pm"]},
    }
    llm_test_index = {
        "version": "2.0",
        "roles": {"pm": {"ready": True, "grade": "PASS", "provider_id": "minimax-1", "model": "MiniMax-M2.5"}},
        "providers": {"minimax-1": {"ready": True, "grade": "PASS", "model": "MiniMax-M2.5"}},
    }
    settings_seed = base64.b64encode(json.dumps(settings, ensure_ascii=False).encode("utf-8")).decode("ascii")
    llm_seed = base64.b64encode(json.dumps(llm_config, ensure_ascii=False).encode("utf-8")).decode("ascii")
    index_seed = base64.b64encode(json.dumps(llm_test_index, ensure_ascii=False).encode("utf-8")).decode("ascii")

    result = _run_node_raw(
        [
            DUAL_ENTRY_FULL_CHAIN_RUNNER,
        ],
        env={
            "KERNELONE_E2E_SETTINGS_JSON_BASE64": settings_seed,
            "KERNELONE_E2E_LLM_CONFIG_JSON_BASE64": llm_seed,
            "KERNELONE_E2E_LLM_TEST_INDEX_JSON_BASE64": index_seed,
            "KERNELONE_E2E_HOME": str(tmp_path / "home"),
            "KERNELONE_E2E_USE_REAL_SETTINGS": "",
        },
    )

    assert result.returncode == 2
    assert "invalid LLM readiness seed" in result.stderr
    assert "model_mismatch" in result.stderr


def test_dual_entry_full_chain_runner_rejects_missing_real_settings_even_for_dry_run() -> None:
    result = _run_node_raw(
        [
            DUAL_ENTRY_FULL_CHAIN_RUNNER,
            "--dry-run",
        ],
        env={"KERNELONE_E2E_USE_REAL_SETTINGS": ""},
    )

    assert result.returncode == 2
    assert "KERNELONE_E2E_USE_REAL_SETTINGS=1 is required" in result.stderr
    assert "not allowed to pass by skipping" in result.stderr


def test_dual_entry_full_chain_runner_rejects_implicit_host_settings_for_non_dry_run() -> None:
    result = _run_node_raw(
        [
            DUAL_ENTRY_FULL_CHAIN_RUNNER,
        ],
        env={
            "KERNELONE_E2E_USE_REAL_SETTINGS": "1",
            "KERNELONE_E2E_SETTINGS_JSON_BASE64": "",
            "KERNELONE_E2E_SETTINGS_JSON": "",
            "KERNELONE_E2E_HOME": "",
            "KERNELONE_HOME": "",
            "KERNELONE_E2E_ALLOW_HOST_SETTINGS": "",
            "PATH": "",
        },
    )

    assert result.returncode == 2
    assert "real LLM settings are required" in result.stderr
    assert "KERNELONE_E2E_SETTINGS_JSON_BASE64" in result.stderr


def test_package_json_exposes_dual_entry_full_chain_runner() -> None:
    package_json = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package_json["scripts"]

    assert scripts["test:e2e:dual-full-chain"] == (
        "node --env-file-if-exists=.env infrastructure/scripts/run-dual-entry-full-chain-e2e.mjs"
    )
    assert scripts["pretest:e2e:dual-full-chain"] == "npm run e2e:prepare"


def test_package_json_exposes_production_stability_runner() -> None:
    package_json = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package_json["scripts"]

    assert scripts["test:e2e:production-stability"] == (
        "node --env-file-if-exists=.env infrastructure/scripts/run-production-stability-validation.mjs"
    )
    assert scripts["pretest:e2e:production-stability"] == "npm run e2e:prepare"


def test_production_stability_runner_dry_run_declares_all_required_gates(tmp_path: Path) -> None:
    audit_path = tmp_path / "production-stability-audit.json"

    result = _run_node(
        [
            PRODUCTION_STABILITY_RUNNER,
            "--dry-run",
            "--output",
            str(audit_path),
        ]
    )

    payload = json.loads(result.stdout)
    gates = {gate["id"]: gate for gate in payload["gates"]}

    assert payload["status"] == "DRY_RUN"
    assert payload["schema"] == "polaris.e2e.production_stability_validation.v1"
    assert payload["output"] == str(audit_path.resolve())
    assert set(gates) == {
        "full_chain",
        "fault_injection_rollback",
        "performance_stress",
        "governance",
    }
    assert gates["full_chain"]["required"] is True
    assert gates["full_chain"]["commands"] == [
        [
            "npm",
            "run",
            "test:e2e:dual-full-chain",
            "--",
            "--require-all-candidate-runtime",
        ]
    ]
    assert "full-chain-audit.spec.ts" in json.dumps(gates["full_chain"]["evidence"], ensure_ascii=True)
    assert "test_transaction_rollback_and_guards.py" in json.dumps(
        gates["fault_injection_rollback"]["evidence"], ensure_ascii=True
    )
    assert "test_v2_endpoint_performance.py" in json.dumps(gates["performance_stress"]["evidence"], ensure_ascii=True)
    assert "run_catalog_governance_gate.py --workspace src/backend --mode hard-fail" in json.dumps(
        gates["governance"]["evidence"], ensure_ascii=True
    )


def test_dual_entry_full_chain_runner_summarizes_existing_desktop_and_web_matrices(tmp_path: Path) -> None:
    summary_path = tmp_path / "dual-summary.json"
    _write_matrix_artifact(tmp_path / "desktop" / "full-chain-expanded-tech-evidence-matrix.json")
    _write_matrix_artifact(tmp_path / "web" / "web-full-chain-expanded-tech-evidence-matrix.json")

    result = _run_node(
        [
            DUAL_ENTRY_FULL_CHAIN_RUNNER,
            "--summarize-existing",
            "--summary-root",
            str(tmp_path),
            "--summary-output",
            str(summary_path),
        ]
    )

    payload = json.loads(result.stdout)
    persisted = json.loads(summary_path.read_text(encoding="utf-8"))

    assert payload["status"] == "PASS"
    assert payload["entrypoints"] == ["desktop", "web"]
    assert payload["matrices"]["desktop"]["placement_rows"] == 16
    assert payload["matrices"]["web"]["placement_rows"] == 16
    assert payload["matrices"]["desktop"]["placement_missing"] == []
    assert payload["matrices"]["web"]["placement_missing"] == []
    assert persisted == payload


def test_dual_entry_full_chain_runner_summary_rejects_missing_web_matrix(tmp_path: Path) -> None:
    _write_matrix_artifact(tmp_path / "desktop" / "full-chain-expanded-tech-evidence-matrix.json")

    result = _run_node_raw(
        [
            DUAL_ENTRY_FULL_CHAIN_RUNNER,
            "--summarize-existing",
            "--summary-root",
            str(tmp_path),
        ]
    )

    assert result.returncode == 1
    assert "missing matrix artifact for web" in result.stderr


def test_dual_entry_full_chain_runner_summary_rejects_missing_placement(tmp_path: Path) -> None:
    _write_matrix_artifact(tmp_path / "desktop" / "full-chain-expanded-tech-evidence-matrix.json")
    _write_matrix_artifact(
        tmp_path / "web" / "web-full-chain-expanded-tech-evidence-matrix.json",
        missing=["acga_graph_cell_governance:audit"],
    )

    result = _run_node_raw(
        [
            DUAL_ENTRY_FULL_CHAIN_RUNNER,
            "--summarize-existing",
            "--summary-root",
            str(tmp_path),
        ]
    )

    assert result.returncode == 1
    assert "web core runtime evidence placement incomplete" in result.stderr
    assert "acga_graph_cell_governance:audit" in result.stderr


def test_dual_entry_full_chain_runner_summary_rejects_stale_matrix_artifacts(tmp_path: Path) -> None:
    desktop_matrix = tmp_path / "desktop" / "full-chain-expanded-tech-evidence-matrix.json"
    web_matrix = tmp_path / "web" / "web-full-chain-expanded-tech-evidence-matrix.json"
    _write_matrix_artifact(desktop_matrix)
    _write_matrix_artifact(web_matrix)
    os.utime(desktop_matrix, (1, 1))
    os.utime(web_matrix, (1, 1))

    result = _run_node_raw(
        [
            DUAL_ENTRY_FULL_CHAIN_RUNNER,
            "--summarize-existing",
            "--summary-root",
            str(tmp_path),
            "--summary-min-mtime-ms",
            "2000",
        ]
    )

    assert result.returncode == 1
    assert "matrix artifact for desktop is older than required minimum mtime" in result.stderr
    assert "matrix artifact for web is older than required minimum mtime" in result.stderr


def test_dual_entry_full_chain_runner_summary_rejects_row_sink_gap_even_when_missing_list_empty(tmp_path: Path) -> None:
    _write_matrix_artifact(tmp_path / "desktop" / "full-chain-expanded-tech-evidence-matrix.json")
    _write_matrix_artifact(
        tmp_path / "web" / "web-full-chain-expanded-tech-evidence-matrix.json",
        broken_row_sink="audit",
    )

    result = _run_node_raw(
        [
            DUAL_ENTRY_FULL_CHAIN_RUNNER,
            "--summarize-existing",
            "--summary-root",
            str(tmp_path),
        ]
    )

    assert result.returncode == 1
    assert "web row acga_graph_cell_governance audit sink not present" in result.stderr


def test_dual_entry_full_chain_runner_summary_rejects_missing_receipt_id(tmp_path: Path) -> None:
    _write_matrix_artifact(tmp_path / "desktop" / "full-chain-expanded-tech-evidence-matrix.json")
    _write_matrix_artifact(
        tmp_path / "web" / "web-full-chain-expanded-tech-evidence-matrix.json",
        receipt_id="",
    )

    result = _run_node_raw(
        [
            DUAL_ENTRY_FULL_CHAIN_RUNNER,
            "--summarize-existing",
            "--summary-root",
            str(tmp_path),
        ]
    )

    assert result.returncode == 1
    assert "web placement receipt_id is missing" in result.stderr


def test_dual_entry_full_chain_runner_summary_rejects_missing_handoff_id(tmp_path: Path) -> None:
    _write_matrix_artifact(tmp_path / "desktop" / "full-chain-expanded-tech-evidence-matrix.json")
    _write_matrix_artifact(
        tmp_path / "web" / "web-full-chain-expanded-tech-evidence-matrix.json",
        handoff_id="",
    )

    result = _run_node_raw(
        [
            DUAL_ENTRY_FULL_CHAIN_RUNNER,
            "--summarize-existing",
            "--summary-root",
            str(tmp_path),
        ]
    )

    assert result.returncode == 1
    assert "web placement handoff_id is missing" in result.stderr


def test_dual_entry_full_chain_runner_summary_rejects_unlinked_task_projection(tmp_path: Path) -> None:
    _write_matrix_artifact(tmp_path / "desktop" / "full-chain-expanded-tech-evidence-matrix.json")
    _write_matrix_artifact(
        tmp_path / "web" / "web-full-chain-expanded-tech-evidence-matrix.json",
        linked_pm_task_count=0,
    )

    result = _run_node_raw(
        [
            DUAL_ENTRY_FULL_CHAIN_RUNNER,
            "--summarize-existing",
            "--summary-root",
            str(tmp_path),
        ]
    )

    assert result.returncode == 1
    assert "web task_projection linked_pm_task_count must be > 0" in result.stderr


def test_dual_entry_full_chain_runner_summary_rejects_missing_candidate_runtime_when_strict(
    tmp_path: Path,
) -> None:
    _write_matrix_artifact(tmp_path / "desktop" / "full-chain-expanded-tech-evidence-matrix.json")
    _write_matrix_artifact(
        tmp_path / "web" / "web-full-chain-expanded-tech-evidence-matrix.json",
        candidate_missing_runtime_ids=["source_candidate"],
    )

    result = _run_node_raw(
        [
            DUAL_ENTRY_FULL_CHAIN_RUNNER,
            "--summarize-existing",
            "--summary-root",
            str(tmp_path),
            "--require-all-candidate-runtime",
        ]
    )

    assert result.returncode == 1
    assert "web candidate runtime coverage incomplete" in result.stderr
    assert "source_candidate" in result.stderr


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
