"""Tests for the factory-bench runner verdict semantics."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.kernelone.benchmark.factory_depth_contract import build_factory_bench_level_contract
from polaris.tests.unit.scripts._factory_bench_runner_audit_helpers import (
    _guard_runner_external_io,
)
from scripts.factory_bench import run_factory_bench as bench
from scripts.factory_bench._bench_lib import (
    cli as bench_cli,
    session as bench_session,
)
from scripts.factory_bench._bench_lib.constants import _FIXTURE
from scripts.factory_bench.run_factory_bench import (
    _desktop_backend_info_path,
    _extract_feature_keywords,
    _fallback_audit_bundle_from_workspace,
    _is_local_backend_url,
    _read_desktop_backend_info,
    _resolve_backend_token,
    _resolve_backend_url,
    _resolve_polaris_home,
    apply_factory_bench_gates,
    build_director_repair_coverage_gap_summary,
    build_requirements_doc,
    load_workspace_validation_repair_coverage,
    map_factory_run_to_chain_results,
)
from scripts.factory_bench.test_bench_to_backend_integration import (
    _consumer_batch,
    _consumer_case,
    _consumer_inputs,
)

_LAST_FACTORY_START_PAYLOAD: dict[str, Any] = {}
_LAST_FACTORY_RESUME_CALL: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def _isolate_instance_registry(monkeypatch: Any, tmp_path: Path) -> None:
    _guard_runner_external_io(monkeypatch, tmp_path)
    monkeypatch.setenv("KERNELONE_INSTANCE_HOME", str(tmp_path / "instances-home"))
    monkeypatch.setenv("FACTORY_BENCH_LAUNCHER_INSTANCE_MODE", "observed")
    monkeypatch.setattr(bench_cli, "persist_real_run_gate_ledger", lambda *_args, **_kwargs: {"ok": True})
    bench.configure_bench_backend("", "", "")


def _bootstrap_test_fact_stream(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="factory_bench_runner_unit_test",
        )
    )


def test_main_default_launcher_mode_uses_isolated_project_backend(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Preserve isolated Supervisor allocation/runtime root and no main/shared backend switch."""
    _, summary, traces = _consumer_batch(tmp_path, monkeypatch)
    inputs, http, supervisor = traces[0]
    request = supervisor.starts[0]
    assert request["backend_port"] is None and request["frontend_port"] is None
    assert request["workspace"] == str(inputs.workspace) and request["require_fresh_instance"] is True
    assert request["runtime_root"] == str(inputs.workspace / ".polaris" / "runtime")
    assert request["metadata"]["backend_binding"] == "isolated_backend_instance"
    assert request["backend_reload"] is False
    assert all("settings" not in path and "/v2/factory/" not in path for _, path, _ in http.calls)
    assert summary["records"][0]["resources_closed"] is True


def test_main_audit_path_points_to_conflict_when_same_id_reused(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Repeated case keeps prior audit bytes immutable through separate fresh batch reports."""
    base = tmp_path / "batch"
    args = ["--dry-run", "--case", "L1-01", "--work-dir", str(base)]
    assert bench_cli.main(args) == 0
    first = next((base / "reports").glob("*/summary.json"))
    original = first.read_bytes()
    assert bench_cli.main(args) == 0
    paths = list((base / "reports").glob("*/summary.json"))
    assert len(paths) == 2 and first.read_bytes() == original
    assert len({json.loads(p.read_text(encoding="utf-8"))["batch_id"] for p in paths}) == 2
    assert not (base / "workspaces").exists()


# --- _resolve_polaris_home ---


def test_resolve_polaris_home_default_uses_dot_polaris() -> None:
    result = _resolve_polaris_home(env={})
    assert result.name == ".polaris"
    assert result == Path.home() / ".polaris"


def test_resolve_polaris_home_kernelone_home_already_dot_polaris(tmp_path: Path) -> None:
    home = tmp_path / ".polaris"
    result = _resolve_polaris_home(env={"KERNELONE_HOME": str(home)})
    assert result == home.expanduser().resolve()


def test_resolve_polaris_home_kernelone_home_parent_dir(tmp_path: Path) -> None:
    parent = tmp_path / "config-root"
    result = _resolve_polaris_home(env={"KERNELONE_HOME": str(parent)})
    expected = parent.expanduser().resolve() / ".polaris"
    assert result == expected


# --- _desktop_backend_info_path ---


def test_desktop_backend_info_path_inside_polaris_home(tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    result = _desktop_backend_info_path(env={"KERNELONE_HOME": str(polaris_home)})
    assert result == polaris_home.expanduser().resolve() / "runtime" / "desktop-backend.json"


# --- _read_desktop_backend_info ---


def test_read_desktop_backend_info_valid_json(tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    runtime = polaris_home / "runtime"
    runtime.mkdir(parents=True)
    info_file = runtime / "desktop-backend.json"
    info_file.write_text(
        json.dumps({"schema_version": 1, "backend": {"baseUrl": "http://127.0.0.1:49977", "token": "tok-123"}}),
        encoding="utf-8",
    )
    result = _read_desktop_backend_info(env={"KERNELONE_HOME": str(polaris_home)})
    assert result["backend"]["baseUrl"] == "http://127.0.0.1:49977"
    assert result["backend"]["token"] == "tok-123"


def test_read_desktop_backend_info_missing_file(tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    result = _read_desktop_backend_info(env={"KERNELONE_HOME": str(polaris_home)})
    assert result == {}


def test_read_desktop_backend_info_malformed_json(tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    runtime = polaris_home / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "desktop-backend.json").write_text("NOT JSON {{{", encoding="utf-8")
    result = _read_desktop_backend_info(env={"KERNELONE_HOME": str(polaris_home)})
    assert result == {}


def test_read_desktop_backend_info_non_dict_json(tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    runtime = polaris_home / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "desktop-backend.json").write_text('"just a string"', encoding="utf-8")
    result = _read_desktop_backend_info(env={"KERNELONE_HOME": str(polaris_home)})
    assert result == {}


# --- _resolve_backend_url desktop fallback ---


def test_resolve_backend_url_falls_back_to_desktop_info(monkeypatch: Any, tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    runtime = polaris_home / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "desktop-backend.json").write_text(
        json.dumps({"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(
        bench_session,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}},
    )
    result = _resolve_backend_url()
    assert result == "http://10.0.0.1:5555"


def test_resolve_backend_url_explicit_overrides_desktop(monkeypatch: Any, tmp_path: Path) -> None:
    polaris_home = tmp_path / ".polaris"
    runtime = polaris_home / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "desktop-backend.json").write_text(
        json.dumps({"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        bench_session,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}},
    )
    result = _resolve_backend_url(explicit="http://192.168.1.1:8080")
    assert result == "http://192.168.1.1:8080"


def test_resolve_backend_url_env_overrides_desktop(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("KERNELONE_BACKEND_URL", "http://env-host:1111")
    monkeypatch.setattr(
        bench_session,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://10.0.0.1:5555", "token": "t"}},
    )
    result = _resolve_backend_url()
    assert result == "http://env-host:1111"


def test_resolve_backend_url_missing_desktop_json_returns_default(monkeypatch: Any) -> None:
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(bench_session, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_url()
    assert result == "http://127.0.0.1:49977"


def test_resolve_backend_url_malformed_desktop_json_returns_default(monkeypatch: Any) -> None:
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(bench_session, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_url()
    assert result == "http://127.0.0.1:49977"


# --- _resolve_backend_token desktop fallback ---


def test_is_local_backend_url_recognizes_loopback_only() -> None:
    assert _is_local_backend_url("http://127.0.0.1:49977") is True
    assert _is_local_backend_url("http://localhost:49977") is True
    assert _is_local_backend_url("http://[::1]:49977") is True
    assert _is_local_backend_url("http://10.0.0.1:49977") is False
    assert _is_local_backend_url("not a url") is False


def test_resolve_backend_token_falls_back_to_desktop_info(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_TOKEN", raising=False)
    monkeypatch.setattr(
        bench_session,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://x", "token": "desktop-tok-abc"}},
    )
    result = _resolve_backend_token()
    assert result == "desktop-tok-abc"


def test_resolve_backend_token_explicit_overrides_desktop(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        bench_session,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://x", "token": "desktop-tok-abc"}},
    )
    result = _resolve_backend_token(explicit="explicit-tok")
    assert result == "explicit-tok"


def test_resolve_backend_token_env_overrides_desktop(monkeypatch: Any) -> None:
    monkeypatch.setenv("FACTORY_BENCH_BACKEND_TOKEN", "env-tok")
    monkeypatch.setattr(
        bench_session,
        "_read_desktop_backend_info",
        lambda env=None: {"backend": {"baseUrl": "http://x", "token": "desktop-tok-abc"}},
    )
    result = _resolve_backend_token()
    assert result == "env-tok"


def test_resolve_backend_token_missing_desktop_json_returns_local_dev_token(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(bench_session, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_token()
    assert result == "polaris-local-dev"


def test_resolve_backend_token_malformed_desktop_json_returns_local_dev_token(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_URL", raising=False)
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_URL", raising=False)
    monkeypatch.setattr(bench_session, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_token()
    assert result == "polaris-local-dev"


def test_resolve_backend_token_missing_remote_token_returns_empty(monkeypatch: Any) -> None:
    monkeypatch.delenv("FACTORY_BENCH_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_TOKEN", raising=False)
    monkeypatch.delenv("KERNELONE_BACKEND_TOKEN", raising=False)
    monkeypatch.setenv("KERNELONE_BACKEND_URL", "http://10.0.0.1:49977")
    monkeypatch.setattr(bench_session, "_read_desktop_backend_info", lambda env=None: {})
    result = _resolve_backend_token()
    assert result == ""


# --- L1-01 regression: contract chain must propagate to Director ---


def test_extract_feature_keywords_from_content_any_checks() -> None:
    """_extract_feature_keywords must extract keywords from content_any checks."""
    project = {
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:3",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    keywords = _extract_feature_keywords(project)
    assert keywords == ["firefly", "flower", "moon", "humidity"]


def test_extract_feature_keywords_no_content_any_returns_empty() -> None:
    """_extract_feature_keywords returns empty list when no content_any checks."""
    project = {"checks": ["ts_syntax", "package_scripts", "min_files:3"]}
    assert _extract_feature_keywords(project) == []


def test_extract_feature_keywords_deduplicates_case_insensitive() -> None:
    """_extract_feature_keywords deduplicates keywords case-insensitively."""
    project = {
        "checks": [
            "content_any:Fire|fire|FLOWER|flower",
        ],
    }
    keywords = _extract_feature_keywords(project)
    assert keywords == ["Fire", "FLOWER"]


def test_l1_01_requirements_doc_preserves_real_source_and_layout_choice() -> None:
    """Real source is required; implementation layout remains a project choice."""
    project = {
        "id": "L1-01",
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "title": "发光昆虫花园模拟器",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "brief": "用 TypeScript 实现发光昆虫花园模拟器",
        "test_focus": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:3",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    doc = build_requirements_doc(project)
    assert "源码与入口" in doc
    assert "框架、算法与模块划分可自行选择" in doc
    assert "不规定唯一目录范式" in doc
    assert "CE 蓝图" not in doc
    assert "领域语义" in doc
    assert "firefly" in doc
    assert "flower" in doc
    assert "moon" in doc
    assert "humidity" in doc
    assert "项目说明" in doc
    assert "science_creative" in doc
    assert "simulation_toy" in doc
    assert "萤火虫根据花朵情绪和月相组成实时灯光舞蹈" in doc
    assert "工程深度要求" in doc
    assert "level: 1" in doc
    assert "min_prod_files: 3" in doc


def test_l1_01_requirements_doc_requires_source_without_legacy_admission() -> None:
    """Executable source is required without a model-authored blueprint or file list."""
    project = {
        "id": "L1-01",
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "title": "发光昆虫花园模拟器",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "brief": "用 TypeScript 实现发光昆虫花园模拟器",
        "test_focus": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:3",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    doc = build_requirements_doc(project)
    assert "真实业务源码与入口" in doc
    assert "target_files" not in doc
    assert "任务合同" not in doc
    assert "不能只包含配置/脚手架" in doc


def test_l1_01_requirements_doc_has_ts_strict_and_features() -> None:
    """Preserve actual TypeScript checks and domain discovery terms."""
    project = {
        "id": "L1-01",
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "title": "发光昆虫花园模拟器",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "brief": "用 TypeScript 实现发光昆虫花园模拟器",
        "test_focus": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:3",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    doc = build_requirements_doc(project)
    assert "TypeScript 运行要求" in doc
    assert "tsc --noEmit" in doc
    assert "领域语义" in doc
    assert "firefly" in doc


def test_build_requirements_doc_python_includes_source_tree() -> None:
    """Python projects retain executable source, tests and depth requirements."""
    project = {
        "id": "L1-03",
        "level": 1,
        "domain": "creative",
        "project_type": "interactive_visual",
        "primary_language": "python",
        "title": "迷你行星天气球",
        "creative_hook": "口袋行星会随云层、风向和昼夜循环改变地表",
        "brief": "用 Python 实现迷你行星天气球",
        "test_focus": "cloud, weather simulation",
        "checks": ["py_compile", "content_any:planet|weather|cloud|wind"],
    }
    doc = build_requirements_doc(project)
    assert "源码与入口" in doc
    assert "框架、算法与模块划分可自行选择" in doc
    assert "不规定唯一目录范式" in doc
    assert "领域语义" in doc
    assert "planet" in doc
    assert "工程深度要求" in doc


def test_l2_requirements_doc_uses_stronger_depth_contract() -> None:
    """L2 requirements must carry an explicit depth floor beyond L1 shape checks."""
    project = {
        "id": "L2-03",
        "level": 2,
        "domain": "creative",
        "project_type": "go_module",
        "primary_language": "go",
        "title": "时间胶囊博物馆",
        "creative_hook": "访客通过谜语解锁不同年代的展柜",
        "brief": "用 Go 实现时间胶囊博物馆",
        "test_focus": "capsule, museum, riddle, unlock",
        "checks": ["go_compile", "min_files:4", "content_any:capsule|museum|riddle|unlock"],
    }

    doc = build_requirements_doc(project)

    assert "工程深度要求" in doc
    assert "level: 2" in doc
    assert "min_prod_files: 6" in doc
    assert "min_prod_lines: 500" in doc
    assert "min_test_assertions: 8" in doc
    assert "Do not satisfy content checks by keyword stuffing" in doc


def test_factory_chain_catalog_contract_writes_metadata(tmp_path: Path) -> None:
    """Catalog contract JSON must include project metadata for PM/CE/Director."""
    project = {
        "id": "L1-01",
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "title": "发光昆虫花园模拟器",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "brief": "用 TypeScript 实现发光昆虫花园模拟器",
        "test_focus": "萤火虫灯光舞蹈",
        "checks": [
            "ts_syntax",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    workspace = tmp_path / "L1-01"
    workspace.mkdir(parents=True, exist_ok=True)

    feature_keywords = _extract_feature_keywords(project)
    raw_level = project.get("level")
    level = raw_level if isinstance(raw_level, int) else int(str(raw_level or 0))
    catalog_contract = {
        "project_id": str(project.get("id") or "").strip(),
        "domain": str(project.get("domain") or "").strip(),
        "project_type": str(project.get("project_type") or "").strip(),
        "primary_language": str(project.get("primary_language") or "").strip(),
        "creative_hook": str(project.get("creative_hook") or "").strip(),
        "feature_keywords": feature_keywords,
        "checks": list(project.get("checks") or []),  # type: ignore[call-overload]
        "test_focus": str(project.get("test_focus") or "").strip(),
        "level": level,
        "level_contract": build_factory_bench_level_contract(level, project=project),
        "source_tree_mandate": "PM/CE/Director must create src/ with core source files, not just scaffolding",
    }
    catalog_path = workspace / ".polaris" / "catalog_contract.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(catalog_contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assert catalog_path.is_file()
    written = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert written["project_id"] == "L1-01"
    assert written["domain"] == "science_creative"
    assert written["project_type"] == "simulation_toy"
    assert written["primary_language"] == "typescript"
    assert written["creative_hook"] == "萤火虫根据花朵情绪和月相组成实时灯光舞蹈"
    assert written["feature_keywords"] == ["firefly", "flower", "moon", "humidity"]
    assert written["level"] == 1
    assert written["level_contract"]["minimums"]["min_prod_files"] == 3
    assert written["source_tree_mandate"] != ""


def test_requirements_preserve_ts_source_and_domain_without_fixed_layout() -> None:
    """Actual TypeScript behavior and tests are required; an undeclared source root is not."""
    project = {
        "id": "L1-01",
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "typescript",
        "title": "发光昆虫花园模拟器",
        "creative_hook": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "brief": "用 TypeScript 实现发光昆虫花园模拟器",
        "test_focus": "萤火虫根据花朵情绪和月相组成实时灯光舞蹈",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:3",
            "content_any:firefly|flower|moon|humidity",
        ],
    }
    doc = build_requirements_doc(project)
    assert "真实业务源码与入口" in doc
    assert "`src/engine/renderer.ts`" not in doc
    assert ".ts" in doc
    assert "firefly" in doc
    assert "flower" in doc
    assert "moon" in doc
    assert "humidity" in doc
    # Tests remain mandatory; the project chooses its directory topology.
    assert "至少一个可执行测试/检查文件" in doc
    assert "不能只包含配置/脚手架" in doc
    assert "框架、算法与模块划分可自行选择" in doc


# --- _fallback_audit_bundle_from_workspace ---


def test_fallback_audit_bundle_from_workspace_reads_dispatch_logs(tmp_path: Path) -> None:
    """Fallback must read .polaris/dispatch/*.log.json and build events/artifacts."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    polaris_dir = workspace / ".polaris"
    dispatch_dir = polaris_dir / "dispatch"
    dispatch_dir.mkdir(parents=True)

    dispatch_log = dispatch_dir / "latest.log.json"
    dispatch_log.write_text(
        json.dumps({"tasks": [{"id": "T1", "status": "done"}]}),
        encoding="utf-8",
    )

    bundle = _fallback_audit_bundle_from_workspace(workspace)

    assert len(bundle["artifacts"]) >= 1
    assert any(a["name"] == "latest.log.json" for a in bundle["artifacts"])
    assert len(bundle["events_tail"]) >= 1
    assert bundle["events_tail"][0]["stage"] == "director_dispatch"
    assert bundle["artifacts"][0]["source"] == "workspace_fallback"


def test_fallback_audit_bundle_from_workspace_reads_roles_director(tmp_path: Path) -> None:
    """Fallback must read .polaris/roles/director/**/*.log.json."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    polaris_dir = workspace / ".polaris"
    roles_dir = polaris_dir / "roles" / "director" / "run_001"
    roles_dir.mkdir(parents=True)

    role_log = roles_dir / "dispatch.log.json"
    role_log.write_text(
        json.dumps({"dispatch": {"status": "ok"}}),
        encoding="utf-8",
    )

    bundle = _fallback_audit_bundle_from_workspace(workspace)

    assert len(bundle["artifacts"]) >= 1
    assert any(a["name"] == "dispatch.log.json" for a in bundle["artifacts"])
    assert len(bundle["events_tail"]) >= 1


def test_fallback_audit_bundle_from_workspace_reads_plan(tmp_path: Path) -> None:
    """Fallback must read .polaris/docs/product/plan.json into summary_json."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    polaris_dir = workspace / ".polaris"
    docs_dir = polaris_dir / "docs" / "product"
    docs_dir.mkdir(parents=True)

    plan = docs_dir / "plan.json"
    plan.write_text(
        json.dumps({"overall_goal": "Build a calculator app"}),
        encoding="utf-8",
    )

    bundle = _fallback_audit_bundle_from_workspace(workspace)

    assert bundle["summary_json"] is not None
    assert bundle["summary_json"]["plan"]["overall_goal"] == "Build a calculator app"
    assert any(a["name"] == "plan.json" for a in bundle["artifacts"])


def test_fallback_audit_bundle_from_workspace_missing_polaris_dir(tmp_path: Path) -> None:
    """Fallback must return empty bundle when .polaris directory is missing."""
    workspace = tmp_path / "ws"
    workspace.mkdir()

    bundle = _fallback_audit_bundle_from_workspace(workspace)

    assert bundle["gates"] == []
    assert bundle["events_tail"] == []
    assert bundle["artifacts"] == []
    assert bundle["summary_json"] is None


def test_run_factory_chain_fallback_on_audit_bundle_timeout(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Read failure preserves unknown/timeout; no artifact fallback may grant authority."""
    inputs, report, http, _ = _consumer_case(tmp_path, state="observation_error")
    assert report["observation"]["harness_status"] == "TIMEOUT"
    assert report["final_observation"]["platform"]["status"] == "cancelled"
    assert report["catalog_quality"] == "UNKNOWN" and report["exit_code"] != 0
    assert all("audit-bundle" not in path and "/v2/factory/" not in path for _, path, _ in http.calls)
    assert not inputs.workspace.exists()


def test_map_director_artifact_preserves_stats_for_display() -> None:
    """Legacy Director stats remain visible but cannot decide execution state."""
    run_status = {"status": "failed", "phase": "director_dispatch"}
    audit_bundle: dict[str, Any] = {
        "gates": [],
        "current_stage": "director_dispatch",
        "summary_json": {"director": {"total": 5, "successes": 2, "failures": 3, "blocked": 0}},
        "director_convergence": {
            "qa_ran": False,
            "blocking_phase": "director_dispatch",
            "missing_delivery_targets": ["quality_gate"],
            "taskboard_final": {"total": 5, "completed": 2, "failed": 3},
            "per_binding_task_status": [
                {"task_id": "T1", "status": "completed"},
                {"task_id": "T2", "status": "completed"},
                {"task_id": "T3", "status": "failed"},
                {"task_id": "T4", "status": "failed"},
                {"task_id": "T5", "status": "failed"},
            ],
        },
    }
    result = map_factory_run_to_chain_results(run_status, audit_bundle)
    assert result["exit_class"] == "legacy_unknown"
    assert result["qa_ran"] is None
    assert result["director"]["total"] == 5
    assert result["director"]["failures"] == 3

    # Verify convergence data is present in the bundle for downstream propagation
    convergence = audit_bundle.get("director_convergence")
    assert convergence is not None
    assert convergence["blocking_phase"] == "director_dispatch"
    assert convergence["missing_delivery_targets"] == ["quality_gate"]
    assert len(convergence["per_binding_task_status"]) == 5


def test_director_repair_coverage_gap_summary_projects_bench_handoff_fields() -> None:
    record = {
        "project_id": "L1-gap",
        "run_id": "run-gap-1",
        "all_checks_passed": True,
        "static_checks_passed": True,
        "chain_results": {"qa_ran": True, "qa_passed": True},
        "has_plan_doc": True,
        "has_blueprint_doc": True,
        "has_qa_verdict": True,
        "wrong_product_suspect": False,
        "backend_freshness": {"ok": True, "detail": "fresh"},
        "real_run_gate": {"ok": True, "summary": "real run passed"},
        "run_ledger_projection": {
            "evidence_policy": {
                "missing_required_modalities": [],
                "failed_required_modalities": [],
            }
        },
        "llm_route_audit": {"ok": True, "summary": "routes ok"},
    }
    audit_bundle = {
        "director_convergence": {
            "repair_kernel": {
                "coverage_report": {
                    "coverage_gap_count": 1,
                    "coverage_gaps": [
                        {
                            "diagnostic": {
                                "diagnostic_id": "diag-ruby-1",
                                "code": "ruby_uninitialized_constant",
                                "path": "app/models/widget.rb",
                                "message": "uninitialized constant Widget",
                            },
                            "diagnostic_language": "ruby",
                            "diagnostic_archetype": "wrong_import_path",
                            "diagnostic_phase": "compiler",
                            "reserved_slot_available": True,
                            "slot_status": "reserved_slot_available",
                            "recommended_route": "runtime_rule",
                            "handoff_recommendation": "runtime_rule_backlog",
                            "recommended_next_owner": "runtime_rule",
                        }
                    ],
                }
            }
        }
    }

    summary = build_director_repair_coverage_gap_summary(record, audit_bundle)
    record["director_repair_coverage_gap_summary"] = summary
    apply_factory_bench_gates(record, chain={"exit_code": 0})

    assert summary["schema_version"] == "factory_bench.director_repair_coverage_gap_summary.v1"
    assert summary["gate_affects_pass"] is False
    assert summary["rule_discovery_required"] is True
    assert summary["coverage_gap_count"] == 1
    assert summary["coverage_gap_languages"] == ["ruby"]
    assert summary["coverage_gap_diagnostic_codes"] == ["ruby_uninitialized_constant"]
    assert summary["coverage_gap_recommended_routes"] == ["runtime_rule"]
    gap = summary["coverage_gaps"][0]
    assert gap["project_id"] == "L1-gap"
    assert gap["run_id"] == "run-gap-1"
    assert gap["diagnostic_language"] == "ruby"
    assert gap["path"] == "app/models/widget.rb"
    assert gap["recommended_next_owner"] == "runtime_rule"
    assert gap["authoritative_rule_registration_allowed"] is False
    factory_gates = record.get("factory_gates")
    assert isinstance(factory_gates, list)
    assert all(gate["gate"] != "director_repair_coverage_gap_summary" for gate in factory_gates)


def test_director_repair_coverage_gap_summary_reads_workspace_validation_artifact(tmp_path: Path) -> None:
    qa_dir = tmp_path / ".polaris" / "qa"
    qa_dir.mkdir(parents=True)
    (qa_dir / "latest.workspace-validation.json").write_text(
        json.dumps(
            {
                "repair": {
                    "director_runtime_repair_coverage": {
                        "coverage_gap_count": 1,
                        "coverage_gaps": [
                            {
                                "diagnostic": {
                                    "diagnostic_id": "diag-go-1",
                                    "code": "go_compile_error",
                                    "path": "engine/riddle.go",
                                    "message": "undefined: Riddle",
                                },
                                "diagnostic_language": "go",
                                "diagnostic_archetype": "missing_dependency",
                                "diagnostic_phase": "quality_repair",
                                "slot_status": "reserved_slot_available",
                                "recommended_route": "runtime_rule",
                                "handoff_recommendation": "runtime_rule_backlog",
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    record = {
        "project_id": "L2-03",
        "run_id": "run-go-gap",
        "workspace_validation_repair_coverage": load_workspace_validation_repair_coverage(tmp_path, []),
    }

    summary = build_director_repair_coverage_gap_summary(record, {})

    coverage = record["workspace_validation_repair_coverage"]
    assert isinstance(coverage, dict)
    assert coverage["report_count"] == 1
    assert summary["coverage_gap_count"] == 1
    assert list(summary["coverage_gap_languages"]) == ["go"]
    assert summary["coverage_gaps"][0]["path"] == "engine/riddle.go"


def test_director_repair_coverage_gap_summary_dedupes_mirrored_validation_reports() -> None:
    gap = {
        "diagnostic": {
            "diagnostic_id": "diag-go-1",
            "code": "workspace_validation_failed",
            "path": "",
            "message": "Workspace validation command failed.",
        },
        "diagnostic_language": "go",
        "diagnostic_archetype": "unknown",
        "diagnostic_phase": "unknown",
        "slot_status": "reserved_slot_available",
        "recommended_route": "llm_repair",
        "handoff_recommendation": "llm_triage_then_runtime_rule",
    }
    report = {"coverage_gap_count": 1, "coverage_gaps": [gap]}
    record = {
        "project_id": "L2-03",
        "run_id": "run-go-gap",
        "workspace_validation_repair_coverage": {
            "reports": [report, report],
        },
    }

    summary = build_director_repair_coverage_gap_summary(record, {})

    assert summary["coverage_gap_count"] == 1
    assert len(summary["coverage_gaps"]) == 1


# --- R18-B: audit snapshot terminal/non-terminal ---


def test_main_start_failed_chain_marks_audit_as_non_terminal(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Failed launch cannot count delivery or continue past uncertain resource fence."""
    code, summary, traces = _consumer_batch(tmp_path, monkeypatch, states=("unknown", "unknown"), fail_start=True)
    assert code == 1 and len(traces) == len(summary["records"]) == 1
    record = summary["records"][0]
    assert record["launch"]["harness_status"] == "UNKNOWN" and record["resources_closed"] is False
    assert record["delivery_proven"] is False and record["catalog_quality"] == "UNKNOWN"


def test_main_event_wait_timeout_marks_non_terminal_and_skips_real_run_gate(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Timeout stays undelivered; owned cancel/cleanup replaces external target gate."""
    code, summary, traces = _consumer_batch(tmp_path, monkeypatch, states=("running",))
    record = summary["records"][0]
    assert code == 1 and record["exit_code"] == 124
    assert record["observation"]["harness_status"] == "TIMEOUT"
    assert record["final_observation"]["platform"]["status"] == "cancelled"
    assert record["resources_closed"] is True and record["catalog_quality"] == "UNKNOWN"
    assert not Path(record["workspace"]).exists()
    assert len([c for c in traces[0][1].calls if c[1].endswith("/cancel")]) == 1


def test_main_runner_exception_marks_audit_as_non_terminal(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Real CLI records unexpected boundary failure as external UNKNOWN, without target effects."""
    seed = _consumer_inputs(tmp_path / "seed")

    def fail_boundary(inputs: Any) -> dict[str, Any]:
        raise RuntimeError("synthetic adapter boundary exception")

    monkeypatch.setattr(bench_cli, "run_engineering_case", fail_boundary)
    base = tmp_path / "batch"
    assert (
        bench_cli.main(
            [
                "--limit",
                "1",
                "--work-dir",
                str(base),
                "--host-config-file",
                str(seed.host_config_file),
                "--llm-config-file",
                str(seed.llm_config_file),
            ]
        )
        == 2
    )
    summary = json.loads(next((base / "reports").glob("*/summary.json")).read_text(encoding="utf-8"))
    record = summary["records"][0]
    assert summary["batch_error"]["harness_status"] == record["harness_status"] == "UNKNOWN"
    assert record["error_type"] == "RuntimeError" and record["resources_closed"] is False
    assert not Path(record["workspace"]).exists()


def test_main_completed_chain_marks_audit_as_terminal(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Current owner/QA/check acceptance plus cleanup remains observable; independent quality unmeasured."""
    code, summary, _ = _consumer_batch(tmp_path, monkeypatch, states=("accepted",))
    record = summary["records"][0]
    assert code == 0 and record["observation"]["harness_status"] == "OBSERVED_ACCEPTED"
    assert record["observation"]["platform"]["acceptance_basis"] == "independent_qa"
    assert record["observation"]["platform"]["checks"][0]["exit_code"] == 0
    assert record["resources_closed"] is True and record["catalog_quality"] == "UNKNOWN"
    assert record["delivery_proven"] is False


# --- Catalog validation tests (from test_projects_v2_catalog.py) ---

REQUIRED_FIELDS = [
    "id",
    "level",
    "domain",
    "project_type",
    "primary_language",
    "title",
    "creative_hook",
    "novelty_tags",
    "brief",
    "test_focus",
    "checks",
]

VALID_LEVELS = set(range(1, 13))
VALID_LANGUAGES = {"typescript", "javascript", "python", "go", "rust", "cpp", "java"}
VALID_DOMAINS = {"science_creative", "creative", "game", "music", "internet_platform"}

LEVEL_MIN_FILES = {
    1: 3,
    2: 4,
    3: 5,
    4: 7,
    5: 8,
    6: 10,
    7: 11,
    8: 12,
    9: 13,
    10: 14,
    11: 15,
    12: 16,
}

LANG_COMPILE_CHECK = {
    "typescript": "ts_syntax",
    "javascript": "js_syntax",
    "python": "py_compile",
    "go": "go_compile",
    "rust": "rust_compile",
    "cpp": "cpp_compile",
    "java": "java_compile",
}


def test_catalog_schema_version() -> None:
    """Validate that projects_v2.json has the expected schema_version."""
    projects_file = Path(bench.__file__).resolve().parent / "projects_v2.json"
    catalog_data = json.loads(projects_file.read_text(encoding="utf-8"))
    version = catalog_data.get("schema_version")
    assert version == "factory-bench/2", f"Unexpected schema_version: {version}"


def test_catalog_hash_is_stable() -> None:
    """Validate that catalog_hash computation is deterministic."""
    projects_file = Path(bench.__file__).resolve().parent / "projects_v2.json"
    catalog_data = json.loads(projects_file.read_text(encoding="utf-8"))
    projects = catalog_data.get("projects", [])

    # Compute hash the same way as run_factory_bench.py
    catalog_hash = hashlib.sha256(json.dumps(projects, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[
        :16
    ]

    # Hash should be non-empty and deterministic
    assert len(catalog_hash) == 16
    assert all(c in "0123456789abcdef" for c in catalog_hash)

    # Compute again to verify determinism
    catalog_hash2 = hashlib.sha256(
        json.dumps(projects, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    assert catalog_hash == catalog_hash2


def test_catalog_has_120_projects() -> None:
    """Validate that catalog contains exactly 120 projects."""
    projects = bench.load_projects()
    assert len(projects) == 120, f"Expected 120 projects, got {len(projects)}"


def test_catalog_no_duplicate_ids() -> None:
    """Validate that catalog has no duplicate project IDs."""
    projects = bench.load_projects()
    ids = [p["id"] for p in projects]
    dupes = [x for x in ids if ids.count(x) > 1]
    assert not dupes, f"Duplicate IDs: {sorted(set(dupes))}"


def test_catalog_all_levels_covered() -> None:
    """Validate that catalog covers all levels L1-L12."""
    projects = bench.load_projects()
    levels = {int(p["level"]) for p in projects}
    missing = VALID_LEVELS - levels
    assert not missing, f"Missing levels: {sorted(missing)}"


def test_catalog_10_projects_per_level() -> None:
    """Validate that each level has exactly 10 projects."""
    projects = bench.load_projects()
    counts = Counter(int(p["level"]) for p in projects)
    for level in VALID_LEVELS:
        assert counts[level] == 10, f"L{level} has {counts[level]} projects, expected 10"


def test_catalog_required_fields_present() -> None:
    """Validate that all required fields are present in each project."""
    projects = bench.load_projects()
    for field in REQUIRED_FIELDS:
        for p in projects:
            assert field in p, f"Project {p.get('id', '?')} missing required field: {field}"


def test_catalog_level_range() -> None:
    """Validate that all project levels are in valid range."""
    projects = bench.load_projects()
    for p in projects:
        level = int(p["level"])
        assert level in VALID_LEVELS, f"Project {p['id']} has invalid level: {level}"


def test_catalog_language_valid() -> None:
    """Validate that all project languages are valid."""
    projects = bench.load_projects()
    for p in projects:
        lang = p["primary_language"]
        assert lang in VALID_LANGUAGES, f"Project {p['id']} has invalid language: {lang}"


def test_catalog_id_format() -> None:
    """Validate that project IDs match the expected format."""
    projects = bench.load_projects()
    for p in projects:
        pid = str(p["id"])
        level = int(p["level"])
        assert pid.startswith(f"L{level}-"), f"ID {pid} doesn't match level {level}"


def test_catalog_min_files_matches_level() -> None:
    """Validate that min_files checks match level expectations."""
    projects = bench.load_projects()
    for p in projects:
        level = int(p["level"])
        checks = p.get("checks", [])
        for check in checks:
            check_str = str(check)
            if check_str.startswith("min_files:"):
                min_files = int(check_str.split(":")[1])
                expected = LEVEL_MIN_FILES.get(level)
                assert min_files == expected, (
                    f"Project {p['id']} (L{level}): min_files={min_files}, expected={expected}"
                )


def test_catalog_compile_check_matches_language() -> None:
    """Validate that compile checks match primary language."""
    projects = bench.load_projects()
    for p in projects:
        lang = p["primary_language"]
        expected = LANG_COMPILE_CHECK.get(lang)
        if not expected:
            continue
        checks = [str(c) for c in p.get("checks", [])]
        assert expected in checks, f"Project {p['id']} ({lang}): missing compile check {expected}"


def test_catalog_content_any_check_present() -> None:
    """Validate that content_any check is present for each project."""
    projects = bench.load_projects()
    for p in projects:
        checks = [str(c) for c in p.get("checks", [])]
        has_content = any(c.startswith("content_any:") for c in checks)
        assert has_content, f"Project {p['id']} missing content_any check"


def test_catalog_source_target_coverage_present() -> None:
    """Validate that source_target_coverage check is present for each project."""
    projects = bench.load_projects()
    for p in projects:
        checks = [str(c) for c in p.get("checks", [])]
        has_coverage = any(c.startswith("source_target_coverage:") for c in checks)
        assert has_coverage, f"Project {p['id']} missing source_target_coverage check"


def test_catalog_language_distribution_balanced() -> None:
    """Validate that language distribution is balanced."""
    projects = bench.load_projects()
    counts = Counter(p["primary_language"] for p in projects)
    min_count = min(counts.values())
    max_count = max(counts.values())
    assert max_count - min_count <= 3, f"Language distribution too uneven: {dict(counts)}"


def test_catalog_novelty_tags_minimum() -> None:
    """Validate that each project has at least 3 novelty tags."""
    projects = bench.load_projects()
    for p in projects:
        tags = p.get("novelty_tags", [])
        assert len(tags) >= 3, f"Project {p['id']} has only {len(tags)} novelty_tags"


def test_catalog_brief_minimum_length() -> None:
    """Validate that each project brief is at least 50 characters."""
    projects = bench.load_projects()
    for p in projects:
        brief = p.get("brief", "")
        assert len(brief) >= 50, f"Project {p['id']} brief too short: {len(brief)} chars"


def test_runner_audit_includes_catalog_hash_and_schema_version(monkeypatch: Any, tmp_path: Path) -> None:
    """Migrated consumer: Original schema/hash/check codes retained; no old audit record becomes new engineering fact."""
    _, summary, traces = _consumer_batch(tmp_path, monkeypatch)
    catalog = Path(_FIXTURE)
    assert summary["catalog_sha256"] == hashlib.sha256(catalog.read_bytes()).hexdigest()
    assert json.loads(catalog.read_text(encoding="utf-8"))["schema_version"] == "factory-bench/2"
    assert summary["catalog_total"] == 120
    assert summary["frozen_inputs"]["catalog"]["sha256"] == summary["catalog_sha256"]
    assert summary["records"][0]["original_project"]["checks"] == [
        "ts_syntax",
        "package_scripts",
        "min_files:3",
        "content_any:firefly|flower|moon|humidity",
        "source_target_coverage:src/**/*.ts",
    ]
    assert traces[0][0].checks_file.read_text(encoding="utf-8").strip() == "[]"


def test_catalog_hash_changes_when_projects_change() -> None:
    """Verify catalog_hash changes when the underlying project data changes."""
    projects_a = [{"id": "L1-01", "level": 1}]
    projects_b = [{"id": "L1-01", "level": 1, "extra": True}]

    hash_a = hashlib.sha256(json.dumps(projects_a, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    hash_b = hashlib.sha256(json.dumps(projects_b, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]

    assert hash_a != hash_b, "catalog_hash must change when project data changes"
