from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "run_agent_headless_stress.py"
    spec = importlib.util.spec_from_file_location("agent_headless_stress_test", str(module_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_headless_stress_test"] = module
    spec.loader.exec_module(module)
    return module


def test_normalize_base_url_strips_v2_suffix() -> None:
    stress = _load_module()
    assert stress._normalize_base_url("http://127.0.0.1:49977/v2") == "http://127.0.0.1:49977"
    assert stress._normalize_base_url("http://127.0.0.1:49977/") == "http://127.0.0.1:49977"


def test_build_workspace_base_rejects_meta_project_without_self_upgrade() -> None:
    stress = _load_module()

    with pytest.raises(ValueError):
        stress._build_workspace_base(str(stress.REPO_ROOT))


@pytest.mark.skipif(os.name != "nt", reason="Windows stress-path policy adds C:/Temp guard even with self-upgrade")
def test_build_workspace_base_rejects_meta_project_even_with_self_upgrade() -> None:
    stress = _load_module()

    with pytest.raises(ValueError):
        stress._build_workspace_base(
            str(stress.REPO_ROOT),
            self_upgrade_mode=True,
        )


def test_build_workspace_base_defaults_to_c_temp_policy_root() -> None:
    stress = _load_module()

    resolved = stress._build_workspace_base("")

    assert resolved == stress.DEFAULT_STRESS_WORKSPACE_BASE


def test_build_ramdisk_root_defaults_to_x_policy_root() -> None:
    stress = _load_module()

    resolved = stress._build_ramdisk_root()

    assert resolved == stress.DEFAULT_STRESS_RAMDISK_ROOT


def test_formal_http_request_allowlist_rejects_unknown_endpoint() -> None:
    stress = _load_module()

    with pytest.raises(ValueError):
        stress._ensure_formal_http_request_allowed("POST", "/v2/pm/run")


def test_formal_http_request_allowlist_accepts_factory_run() -> None:
    stress = _load_module()

    stress._ensure_formal_http_request_allowed("POST", "/v2/factory/runs")
    stress._ensure_formal_http_request_allowed("GET", "/v2/factory/runs/run-1/events")


def test_prepare_target_workspace_rejects_non_empty_directory(tmp_path: Path) -> None:
    stress = _load_module()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "README.md").write_text("# seeded\n", encoding="utf-8")

    with pytest.raises(ValueError):
        stress._prepare_target_workspace(workspace)


def test_prepare_target_workspace_allows_empty_directory(tmp_path: Path) -> None:
    stress = _load_module()
    workspace = tmp_path / "workspace"

    resolved = stress._prepare_target_workspace(workspace)

    assert resolved == workspace.resolve()
    assert resolved.is_dir()
    assert list(resolved.iterdir()) == []


def test_desktop_backend_info_path_uses_polaris_home_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stress = _load_module()
    home = tmp_path / ".polaris"
    monkeypatch.setenv("KERNELONE_HOME", str(home))

    resolved = stress._desktop_backend_info_path()

    assert resolved == (home / "runtime" / "desktop-backend.json").resolve()


def test_resolve_backend_connection_falls_back_to_desktop_backend_info(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stress = _load_module()
    home = tmp_path / ".polaris"
    backend_info_path = home / "runtime" / "desktop-backend.json"
    backend_info_path.parent.mkdir(parents=True, exist_ok=True)
    backend_info_path.write_text(
        stress.json.dumps(
            {
                "schema_version": 1,
                "source": "electron_main",
                "updated_at": "2026-03-08T00:00:00Z",
                "state": "running",
                "ready": True,
                "backend": {
                    "baseUrl": "http://127.0.0.1:51871",
                    "token": "secret-token",
                    "port": 51871,
                    "pid": 135596,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KERNELONE_HOME", str(home))

    base_url, token, metadata = asyncio.run(
        stress._resolve_backend_connection("", "", discovery_timeout_seconds=0),
    )

    assert base_url == "http://127.0.0.1:51871"
    assert token == "secret-token"
    assert metadata["source"] == "desktop_backend_info"
    assert metadata["path"] == str(backend_info_path.resolve())


def test_resolve_backend_connection_prefers_explicit_values_over_desktop_backend_info(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stress = _load_module()
    home = tmp_path / ".polaris"
    backend_info_path = home / "runtime" / "desktop-backend.json"
    backend_info_path.parent.mkdir(parents=True, exist_ok=True)
    backend_info_path.write_text(
        stress.json.dumps(
            {
                "schema_version": 1,
                "source": "electron_main",
                "updated_at": "2026-03-08T00:00:00Z",
                "state": "running",
                "ready": True,
                "backend": {
                    "baseUrl": "http://127.0.0.1:51871",
                    "token": "desktop-token",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KERNELONE_HOME", str(home))

    base_url, token, metadata = asyncio.run(
        stress._resolve_backend_connection(
            "http://127.0.0.1:49977",
            "explicit-token",
            discovery_timeout_seconds=0,
        ),
    )

    assert base_url == "http://127.0.0.1:49977"
    assert token == "explicit-token"
    assert metadata["source"] == "cli_or_env"


def test_build_round_directive_is_agent_specific() -> None:
    stress = _load_module()
    scenario = stress._pick_scenario(3)
    directive = stress._build_round_directive(
        scenario,
        round_number=4,
        agent_label="codex",
        complexity_floor_lines=600,
    )
    lowered = directive.lower()
    assert "codex" in lowered
    assert "integration_qa_passed" in directive
    assert "`id`" in directive
    assert "metadata.pm_task_id" in directive
    assert "600" in directive


def test_detect_prompt_leakage_reads_json_string_leaves() -> None:
    stress = _load_module()
    payload = {
        "summary": "Do not expose the system prompt.",
        "details": {"note": "you are the PM"},
    }
    findings = stress._detect_prompt_leakage(
        stress.json.dumps(payload, ensure_ascii=False),
        "runtime/contracts/pm_tasks.contract.json",
    )
    keywords = {item["keyword"] for item in findings}
    assert "system prompt" in keywords
    assert "you are" in keywords


def test_validate_pm_contract_rejects_invalid_tasks() -> None:
    stress = _load_module()
    pm_contract = {
        "quality_gate": {"score": 92, "critical_issue_count": 0, "summary": "ok"},
        "tasks": [
            {
                "goal": "Build service",
                "scope_paths": ["src/service.py"],
                "execution_checklist": ["implement", "test"],
                "acceptance_criteria": ["pytest -q passes"],
            },
            {
                "goal": "Broken task",
                "scope_paths": [],
                "execution_checklist": [],
                "acceptance": [],
            },
        ],
    }
    result = stress._validate_pm_contract(pm_contract)
    assert result["invalid_task_count"] == 1
    assert result["passed"] is False


def test_count_director_lineage_requires_pm_task_id() -> None:
    stress = _load_module()
    result = stress._count_director_lineage(
        [
            {"id": "D1", "metadata": {"pm_task_id": "PM-1"}},
            {"id": "D2", "metadata": {}},
        ]
    )
    assert result["total_tasks"] == 2
    assert result["linked_task_count"] == 1
    assert result["passed"] is True


def test_build_report_aggregates_round_coverage() -> None:
    stress = _load_module()
    round_one = stress.RoundReport(
        round=1,
        project_name="实时聊天室（WebSocket）",
        category="realtime",
        enhancements=["消息历史"],
        workspace="C:/Temp/ws-1",
        directive_path="C:/Temp/ws-1/directive.md",
        result="PASS",
    )
    round_two = stress.RoundReport(
        round=2,
        project_name="博客系统（CMS）",
        category="cms",
        enhancements=["分类标签"],
        workspace="C:/Temp/ws-2",
        directive_path="C:/Temp/ws-2/directive.md",
        result="FAIL",
        qa_result={"passed": False, "reason": "integration_qa_failed"},
    )
    report = stress._build_report(
        agent_label="claude",
        base_url="http://127.0.0.1:49977",
        workspace_base=Path("C:/Temp/PolarisAgentStress"),
        stable_required=1,
        requested_rounds=2,
        rounds=[round_one, round_two],
    )
    assert report["coverage_summary"]["projects_completed"] == 1
    assert report["coverage_summary"]["projects_failed"] == 1
    assert "cms" in report["coverage_summary"]["categories_covered"]
    assert report["status"] == "PASS"


@pytest.mark.skipif(os.name != "nt", reason="Windows stress-path policy validates C:/Temp before backend discovery")
def test_run_async_validates_workspace_policy_before_backend_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stress = _load_module()

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("backend discovery should not run before workspace policy validation")

    monkeypatch.setattr(stress, "_resolve_backend_connection", fail_if_called)
    args = SimpleNamespace(
        base_url="",
        token="",
        workspace_base=str(stress.REPO_ROOT),
        self_upgrade_mode=True,
        rounds=1,
        stable_required=1,
        start_from="auto",
        director_iterations=1,
        round_timeout_seconds=60,
        poll_interval_seconds=1.0,
        scenario_offset=0,
        complexity_floor_lines=500,
        report_output="",
        agent_label="codex",
    )

    exit_code = asyncio.run(stress._run_async(args))

    assert exit_code == 2
