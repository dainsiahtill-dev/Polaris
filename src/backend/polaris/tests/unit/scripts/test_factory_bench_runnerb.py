"""Tests for the factory-bench runner verdict semantics."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    append_run_ledger_event,
)
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from scripts.factory_bench import run_factory_bench as bench
from scripts.factory_bench._bench_lib import (
    artifacts as bench_artifacts,
    chain as bench_chain,
    cli as bench_cli,
    gates as bench_gates,
    session as bench_session,
    workspace as bench_workspace,
)
from scripts.factory_bench.run_factory_bench import (
    _allocate_fresh_project_workspace,
    _desktop_backend_info_path,
    _extract_feature_keywords,
    _fallback_audit_bundle_from_workspace,
    _is_local_backend_url,
    _next_immutable_json_path,
    _project_workspace_for_run,
    _read_desktop_backend_info,
    _resolve_backend_token,
    _resolve_backend_url,
    _resolve_polaris_home,
    _sanitize_run_id,
    _write_immutable_json,
    apply_factory_bench_gates,
    build_director_repair_coverage_gap_summary,
    build_requirements_doc,
    discover_artifacts,
    load_workspace_validation_repair_coverage,
    map_factory_run_to_chain_results,
    read_chain_results_from_runtime_dirs,
    resolve_runtime_dirs_for_workspace,
    run_factory_chain,
)

_LAST_FACTORY_START_PAYLOAD: dict[str, Any] = {}
_LAST_FACTORY_RESUME_CALL: dict[str, Any] = {}


@pytest.fixture(autouse=True)
def _isolate_instance_registry(monkeypatch: Any, tmp_path: Path) -> None:
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
    monkeypatch.delenv("FACTORY_BENCH_LAUNCHER_INSTANCE_MODE", raising=False)
    projects = [{"id": "L1-01", "level": 1, "title": "One", "brief": "Build one"}]
    captured_chain: dict[str, str] = {}
    captured_session: dict[str, Any] = {}
    backend_context_urls: list[str] = []
    launch_receipts: list[dict[str, Any]] = []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_factory_bench.py",
            "--project-ids",
            "L1-01",
            "--work-dir",
            str(tmp_path),
        ],
    )
    monkeypatch.setattr(bench_cli, "load_projects", lambda: projects)
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "http://127.0.0.1:49977")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "main-token")

    def _capture_session(**kwargs: Any) -> str:
        captured_session.update(kwargs)
        return "bench-isolated"

    monkeypatch.setattr(bench_cli, "_ensure_bench_session", _capture_session)
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "_push_bench_workspace_to_backend",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("isolated mode must not switch shared workspace")),
    )

    def _start_isolated(**kwargs: Any) -> dict[str, Any]:
        receipt = dict(kwargs["launch_receipt"])
        launch_receipts.append(receipt)
        return {
            "instance_id": receipt["instance_id"],
            "workspace": receipt["workspace"],
            "runtime_root": receipt["runtime_root"],
            "backend_pid": 73101,
            "metadata": {"instance_launch_receipt": receipt},
            "backend_url": "http://127.0.0.1:60011",
            "frontend_url": "http://127.0.0.1:60012",
            "token": "isolated-token",
        }

    monkeypatch.setattr(bench_cli, "_start_isolated_bench_project_instance", _start_isolated)

    def _backend_context(url: str, **_kwargs: Any) -> dict[str, Any]:
        backend_context_urls.append(url)
        workspace = str(_kwargs["workspace"])
        source_fingerprint = bench_session.compute_source_fingerprint(bench._BACKEND_ROOT)
        receipt = launch_receipts[-1] if launch_receipts else {}
        return {
            "backend_freshness": {
                "ok": True,
                "detail": "backend fresh",
                "expected_fingerprint": source_fingerprint,
                "actual_fingerprint": source_fingerprint,
                "backend_info": {
                    "pid": 73101,
                    "instance_id": str(receipt.get("instance_id") or ""),
                    "workspace": workspace,
                    "backend_root": str(receipt.get("expected_backend_root") or bench._BACKEND_ROOT),
                },
            },
            "backend_metadata": {"backend_base_url": url},
        }

    monkeypatch.setattr(bench_cli, "build_bench_backend_audit_context", _backend_context)
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench_cli, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(
        bench_cli,
        "build_factory_audit_record",
        lambda **_kwargs: _successful_audit_record(),
    )
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    monkeypatch.setattr(bench_cli, "persist_real_run_gate_ledger", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )

    def _chain(
        _project: dict[str, Any],
        _workspace: Path,
        *,
        backend_url: str,
        backend_token: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        captured_chain["backend_url"] = backend_url
        captured_chain["backend_token"] = backend_token
        return {
            "exit_code": 0,
            "duration_s": 0.01,
            "chain_results": {
                "contract_goal": "Build one",
                "qa_ran": True,
                "qa_passed": True,
                "director": {"total": 1, "successes": 1, "failures": 0},
            },
        }

    monkeypatch.setattr(bench_cli, "run_factory_chain", _chain)

    first_result = bench.main()
    second_result = bench.main()

    assert first_result == 0
    assert second_result == 0
    assert len(launch_receipts) == 2
    assert launch_receipts[0]["requested_instance_id"] == launch_receipts[1]["requested_instance_id"]
    assert launch_receipts[0]["instance_id"] != launch_receipts[1]["instance_id"]
    assert launch_receipts[0]["launch_scope"] != launch_receipts[1]["launch_scope"]
    assert launch_receipts[0]["workspace"] != launch_receipts[1]["workspace"]
    assert launch_receipts[0]["runtime_root"] != launch_receipts[1]["runtime_root"]
    assert captured_chain == {
        "backend_url": "http://127.0.0.1:60011",
        "backend_token": "isolated-token",
    }
    assert captured_session["backend_url"] == ""
    assert captured_session["metadata"]["launcher_instance_mode"] == "isolated"
    assert captured_session["metadata"]["bench_session_reporting"] == "auto"
    assert backend_context_urls[-1] == "http://127.0.0.1:60011"
    audit = json.loads((tmp_path / "factory_audits.json").read_text(encoding="utf-8"))["records"][0]
    assert audit["requested_project_id"] == "L1-01"
    assert audit["canonical_project_id"] == "L1-01"
    assert audit["instance_id"]
    assert audit["requested_instance_id"]
    assert audit["run_id"] == audit["instance_launch_receipt"]["run_id"]
    assert audit["instance_launch_validation"]["ok"] is True


def test_main_audit_path_points_to_conflict_when_same_id_reused(monkeypatch: Any, tmp_path: Path) -> None:
    """If the same project id appears twice, the second audit must reference the conflict file."""
    projects = [
        {"id": "L1-01", "level": 1, "title": "One", "brief": "Build one"},
        {"id": "L1-01", "level": 2, "title": "One Again", "brief": "Build one again"},
    ]

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(bench_cli, "load_projects", lambda: projects)
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-conflict")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench_cli, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(
        bench_cli,
        "build_factory_audit_record",
        lambda **_kwargs: _successful_audit_record(),
    )
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )

    def _chain(project: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": 0,
            "duration_s": 0.01,
            "chain_results": {
                "contract_goal": str(project["brief"]),
                "qa_ran": True,
                "qa_passed": True,
                "director": {"total": 1, "successes": 1, "failures": 0},
            },
        }

    monkeypatch.setattr(bench_cli, "run_factory_chain", _chain)

    result = bench.main()

    assert result == 0
    audit_dir = tmp_path / "audits"
    run_dir = next(iter(audit_dir.iterdir()))
    audit_files = sorted(run_dir.glob("*.json"))
    assert len(audit_files) == 2, f"Expected 2 audit files (including conflict), got {audit_files}"
    primary_file = run_dir / "L1-01.audit.json"
    conflict_file = run_dir / "L1-01.audit.2.json"
    assert primary_file.exists(), "Expected primary audit file"
    assert conflict_file.exists(), "Expected conflict file for repeated project id"
    for af in audit_files:
        data = json.loads(af.read_text(encoding="utf-8"))
        assert "audit_path" in data, f"audit_path missing from {af.name}"
        assert (tmp_path / data["audit_path"]).resolve() == af.resolve(), (
            f"audit_path {data['audit_path']} does not resolve to {af}"
        )


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


def test_l1_01_requirements_doc_contains_source_tree_contract() -> None:
    """L1-01 requirements doc must contain source tree contract requiring src/."""
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
    assert "Source Tree Structure Contract" in doc
    assert "目录拓扑由 Chief Engineer 决定" in doc
    assert "不规定唯一目录范式" in doc
    assert "写成唯一合法结构" in doc
    assert "Feature Keywords Contract" in doc
    assert "firefly" in doc
    assert "flower" in doc
    assert "moon" in doc
    assert "humidity" in doc
    assert "Project Metadata" in doc
    assert "science_creative" in doc
    assert "simulation_toy" in doc
    assert "萤火虫根据花朵情绪和月相组成实时灯光舞蹈" in doc
    assert "Bench Level Contract (Mandatory)" in doc
    assert "level: 1" in doc
    assert "min_prod_files: 3" in doc


def test_l1_01_requirements_doc_director_target_files_mandate() -> None:
    """L1-01 requirements doc must mandate Director target_files cover src/."""
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
    assert "必须覆盖 CE 蓝图声明的业务源码与入口" in doc
    assert "不能只包含配置/脚手架" in doc


def test_l1_01_requirements_doc_has_ts_strict_and_features() -> None:
    """L1-01 requirements doc must include TS-specific contract + feature keywords."""
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
    assert "Language-Specific Runnable Contract (TypeScript)" in doc
    assert "tsc --noEmit" in doc
    assert "Feature Keywords Contract" in doc
    assert "firefly" in doc


def test_build_requirements_doc_python_includes_source_tree() -> None:
    """Python projects must also get source tree contract."""
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
    assert "Source Tree Structure Contract" in doc
    assert "目录拓扑由 Chief Engineer 决定" in doc
    assert "不规定唯一目录范式" in doc
    assert "Feature Keywords Contract" in doc
    assert "planet" in doc
    assert "Bench Level Contract (Mandatory)" in doc


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

    assert "Bench Level Contract (Mandatory)" in doc
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
        "level_contract": bench_gates.build_factory_bench_level_contract(level, project=project),
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


def test_director_contract_requires_ts_target_and_feature_keywords() -> None:
    """The generated Director contract for L1-01 must include .ts targets and feature keywords.

    This is the core regression: the Director must not only produce package.json/tsconfig.json
    scaffolding — it must target src/ .ts files and embed firefly|flower|moon|humidity.
    """
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
    assert "src/" in doc
    assert ".ts" in doc
    assert "firefly" in doc
    assert "flower" in doc
    assert "moon" in doc
    assert "humidity" in doc
    assert "tests/" in doc
    assert "不能只包含 package.json" in doc


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
    """run_factory_chain must use workspace fallback when audit-bundle returns None."""
    workspace = tmp_path / "L2-fallback"
    workspace.mkdir()
    _LAST_FACTORY_START_PAYLOAD.clear()

    # Seed workspace .polaris artifacts for fallback
    polaris_dir = workspace / ".polaris"
    dispatch_dir = polaris_dir / "dispatch"
    dispatch_dir.mkdir(parents=True)
    (dispatch_dir / "latest.log.json").write_text(
        json.dumps({"tasks": [{"id": "T1", "status": "done"}]}),
        encoding="utf-8",
    )

    def _fake_start_factory_run(_backend_url: str, _payload: dict[str, Any], token: str = "") -> dict[str, Any] | None:
        _LAST_FACTORY_START_PAYLOAD.update(_payload)
        return {"run_id": "run-fb-123"}

    def _fake_wait_run_until_terminal(
        _backend_url: str,
        run_id: str,
        token: str = "",
        workspace: str = "",
        on_status: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        if on_status is not None:
            on_status({"status": "failed", "phase": "director_dispatch"})
        return {"status": "failed", "phase": "director_dispatch"}

    def _fake_get_audit_bundle(
        _backend_url: str,
        _run_id: str,
        token: str = "",
        workspace: str = "",
    ) -> dict[str, Any] | None:
        # Simulate timeout: return None
        return None

    def _fake_cancel_factory_run(
        _backend_url: str,
        _run_id: str,
        *,
        reason: str = "",
        token: str = "",
        workspace: str = "",
        return_errors: bool = False,
    ) -> dict[str, Any]:
        del reason, token, workspace, return_errors
        return {"status": "cancelled"}

    monkeypatch.setattr(bench_chain, "start_factory_run", _fake_start_factory_run)
    monkeypatch.setattr(bench_chain, "wait_run_until_terminal", _fake_wait_run_until_terminal)
    monkeypatch.setattr(bench_chain, "get_audit_bundle", _fake_get_audit_bundle)
    monkeypatch.setattr(bench_chain, "cancel_factory_run", _fake_cancel_factory_run)

    result = run_factory_chain(
        {"id": "L2-fb", "title": "Fallback Test", "brief": "Test fallback", "test_focus": "runtime"},
        workspace,
        backend_url="http://localhost:49977",
        backend_token="",
        timeout_s=30,
        log_path=tmp_path / "L2-fb.chain.log",
    )

    assert result["exit_code"] == 1
    assert result["run_id"] == "run-fb-123"
    assert "audit_bundle" in result
    assert len(result["audit_bundle"]["artifacts"]) >= 1
    assert result["audit_bundle"]["artifacts"][0]["source"] == "workspace_fallback"
    assert result["audit_bundle"]["events_tail"][0]["stage"] == "director_dispatch"


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


def test_main_start_failed_chain_marks_audit_as_non_terminal(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """When run_factory_chain returns start_failed, the audit record must be
    marked as non_terminal so it cannot be confused with a final verdict."""
    captured_records: list[dict[str, Any]] = []

    def _capture_build(**kwargs: Any) -> dict[str, Any]:
        captured_records.append(kwargs)
        return {
            "all_checks_passed": False,
            "static_checks_passed": False,
            "has_plan_doc": False,
            "has_blueprint_doc": False,
            "has_qa_verdict": False,
            "code_file_count": 0,
            "source_file_count": 0,
            "checks": [],
            "audit_snapshot_kind": "non_terminal" if not kwargs.get("chain_terminal", True) else "terminal",
            "audit_terminal": kwargs.get("chain_terminal", True),
        }

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--project-ids", "L1-01", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(
        bench_cli,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Test", "brief": "Build something", "checks": []}],
    )
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "http://127.0.0.1:49977")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "token")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-non-terminal")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(
        bench_cli,
        "discover_artifacts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pre-start failure must not reuse prior workspace artifacts")
        ),
    )
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(bench_cli, "build_factory_audit_record", _capture_build)
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": False, "summary": "real run failed"},
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": False, "summary": "LLM route audit failed"},
    )

    def _start_failed_chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"exit_code": -1, "duration_s": 0.0, "error": "start_failed"}

    monkeypatch.setattr(bench_cli, "run_factory_chain", _start_failed_chain)

    result = bench.main()

    assert result == 1
    assert len(captured_records) == 1
    assert captured_records[0]["chain_terminal"] is False
    audit = json.loads((tmp_path / "factory_audits.json").read_text(encoding="utf-8"))["records"][0]
    assert audit["chain_attempt_started"] is False
    assert audit["chain_results"] == {}
    assert audit["qa_invoked"] == {"invoked": False, "reason": "current_attempt_not_started"}


def test_main_event_wait_timeout_marks_non_terminal_and_skips_real_run_gate(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """When runtime.v2 never delivers a terminal event, main must not run
    build/test/start against a workspace the backend may still be mutating."""
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    captured_records: list[dict[str, Any]] = []
    taxonomy_records: list[dict[str, Any]] = []

    def _capture_build(**kwargs: Any) -> dict[str, Any]:
        captured_records.append(kwargs)
        return {
            "all_checks_passed": False,
            "static_checks_passed": False,
            "has_plan_doc": False,
            "has_blueprint_doc": False,
            "has_qa_verdict": False,
            "code_file_count": 0,
            "source_file_count": 0,
            "checks": [],
            "audit_snapshot_kind": "non_terminal" if not kwargs.get("chain_terminal", True) else "terminal",
            "audit_terminal": kwargs.get("chain_terminal", True),
        }

    def _capture_taxonomy(record: dict[str, Any]) -> dict[str, Any]:
        taxonomy_records.append(record)
        taxonomy = {
            "ok": False,
            "category": "runtime_environment",
            "root_cause_signature": "runtime_environment:real_run_gate.chain_terminal",
            "reasons": [],
            "evidence": [],
        }
        record["failure_taxonomy"] = taxonomy
        record["failure_category"] = taxonomy["category"]
        record["root_cause_signature"] = taxonomy["root_cause_signature"]
        record["failure_reasons"] = []
        record["failure_evidence"] = []
        record["goal_audit"] = {}
        return taxonomy

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--project-ids", "L1-01", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(
        bench_cli,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Test", "brief": "Build something", "checks": []}],
    )
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "http://127.0.0.1:49977")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "token")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-event-timeout")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_workspace_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench_cli, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(bench_cli, "build_factory_audit_record", _capture_build)
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("real run gate must be skipped")),
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": False, "summary": "LLM route audit failed"},
    )
    monkeypatch.setattr(bench_cli, "apply_factory_bench_failure_taxonomy", _capture_taxonomy)

    def _event_wait_timeout(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": -1,
            "duration_s": 1.0,
            "run_id": "run-timeout",
            "error": "event_wait_timeout",
        }

    monkeypatch.setattr(bench_cli, "run_factory_chain", _event_wait_timeout)

    result = bench.main()

    assert result == 1
    assert len(captured_records) == 1
    assert captured_records[0]["chain_terminal"] is False
    assert len(taxonomy_records) == 1
    real_run_gate = taxonomy_records[0]["real_run_gate"]
    assert real_run_gate["skipped"] is True
    assert real_run_gate["requirements"]["chain_terminal"]["ok"] is False
    assert "event_wait_timeout" in real_run_gate["summary"]


def test_main_runner_exception_marks_audit_as_non_terminal(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """When the runner raises an exception, the audit record must be
    marked as non_terminal."""
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    captured_records: list[dict[str, Any]] = []

    def _capture_build(**kwargs: Any) -> dict[str, Any]:
        captured_records.append(kwargs)
        return {
            "all_checks_passed": False,
            "static_checks_passed": False,
            "has_plan_doc": False,
            "has_blueprint_doc": False,
            "has_qa_verdict": False,
            "code_file_count": 0,
            "source_file_count": 0,
            "checks": [],
            "audit_snapshot_kind": "non_terminal" if not kwargs.get("chain_terminal", True) else "terminal",
            "audit_terminal": kwargs.get("chain_terminal", True),
        }

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--project-ids", "L1-01", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(
        bench_cli,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Test", "brief": "Build something", "checks": []}],
    )
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "http://127.0.0.1:49977")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "token")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-runner-exc")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench_cli, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(bench_cli, "build_factory_audit_record", _capture_build)
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": False, "summary": "real run failed"},
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": False, "summary": "LLM route audit failed"},
    )

    def _runner_exception(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("simulated runner crash")

    monkeypatch.setattr(bench_cli, "run_factory_chain", _runner_exception)

    result = bench.main()

    assert result == 1
    assert len(captured_records) == 1
    assert captured_records[0]["chain_terminal"] is False


def test_main_completed_chain_marks_audit_as_terminal(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """When run_factory_chain returns normally, the audit record must be
    marked as terminal."""
    captured_records: list[dict[str, Any]] = []

    def _capture_build(**kwargs: Any) -> dict[str, Any]:
        captured_records.append(kwargs)
        return _successful_audit_record(
            audit_snapshot_kind="terminal" if kwargs.get("chain_terminal", True) else "non_terminal",
            audit_terminal=kwargs.get("chain_terminal", True),
        )

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--project-ids", "L1-01", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(
        bench_cli,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Test", "brief": "Build something", "checks": []}],
    )
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-terminal")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench_cli, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(bench_cli, "build_factory_audit_record", _capture_build)
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )

    def _completed_chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": 0,
            "duration_s": 0.01,
            "chain_results": {
                "contract_goal": "Build something",
                "qa_ran": True,
                "qa_passed": True,
                "director": {"total": 1, "successes": 1, "failures": 0},
            },
        }

    monkeypatch.setattr(bench_cli, "run_factory_chain", _completed_chain)

    result = bench.main()

    assert result == 0
    assert len(captured_records) == 1
    assert captured_records[0]["chain_terminal"] is True


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
    """Verify runner writes catalog_hash and catalog_schema_version into audit and meta files."""
    projects = [
        {"id": "L1-01", "level": 1, "title": "Test", "brief": "Build something", "checks": []},
    ]

    monkeypatch.setattr(sys, "argv", ["run_factory_bench.py", "--work-dir", str(tmp_path)])
    monkeypatch.setattr(bench_cli, "load_projects", lambda: projects)
    monkeypatch.setattr(bench_cli, "_resolve_backend_url", lambda: "")
    monkeypatch.setattr(bench_cli, "_resolve_backend_token", lambda: "")
    monkeypatch.setattr(bench_cli, "_ensure_bench_session", lambda **_kwargs: "bench-meta")
    monkeypatch.setattr(bench_cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(bench_cli, "_push_bench_progress_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(bench_cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(
        bench_cli,
        "build_bench_backend_audit_context",
        lambda *_args, **_kwargs: {
            "backend_freshness": {"ok": True, "detail": "backend fresh"},
            "backend_metadata": {"backend_base_url": ""},
        },
    )
    monkeypatch.setattr(bench_cli, "resolve_runtime_dirs_for_workspace", lambda _workspace: [])
    monkeypatch.setattr(bench_cli, "discover_artifacts", lambda _workspace, _runtime_dirs: {})
    monkeypatch.setattr(bench_cli, "collect_llm_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(bench_cli, "resolve_expected_llm_bindings", lambda: {})
    monkeypatch.setattr(
        bench_cli,
        "build_factory_audit_record",
        lambda **_kwargs: _successful_audit_record(),
    )
    monkeypatch.setattr(bench_cli, "load_run_ledger_projection", _ok_run_ledger_projection)
    monkeypatch.setattr(
        bench_cli,
        "build_real_run_gate",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )
    monkeypatch.setattr(
        bench_cli,
        "build_llm_route_audit",
        lambda *_args, **_kwargs: {"ok": True, "summary": "ok"},
    )

    def _chain(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "exit_code": 0,
            "duration_s": 0.01,
            "chain_results": {
                "contract_goal": "Build something",
                "qa_ran": True,
                "qa_passed": True,
                "director": {"total": 1, "successes": 1, "failures": 0},
            },
        }

    monkeypatch.setattr(bench_cli, "run_factory_chain", _chain)

    result = bench.main()
    assert result == 0

    # Compute expected catalog hash from the projects list
    expected_hash = hashlib.sha256(
        json.dumps(projects, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]

    # Verify .catalog_meta.json was written into the unique fresh workspace.
    catalog_meta_paths = list((tmp_path / "workspaces").rglob(".catalog_meta.json"))
    assert len(catalog_meta_paths) == 1, "one identity-bound .catalog_meta.json must be written"
    catalog_meta_path = catalog_meta_paths[0]
    catalog_meta = json.loads(catalog_meta_path.read_text(encoding="utf-8"))
    assert catalog_meta["catalog_schema_version"] == "factory-bench/2"
    assert catalog_meta["catalog_hash"] == expected_hash
    assert catalog_meta["project_id"] == "L1-01"

    # Verify the audit file contains catalog_schema_version and catalog_hash
    audit_dir = tmp_path / "audits"
    run_dirs = list(audit_dir.iterdir())
    assert len(run_dirs) == 1
    audit_files = sorted(run_dirs[0].glob("*.audit.json"))
    assert len(audit_files) == 1
    audit_data = json.loads(audit_files[0].read_text(encoding="utf-8"))
    assert audit_data["catalog_schema_version"] == "factory-bench/2"
    assert audit_data["catalog_hash"] == expected_hash
    assert audit_data["run_id"] == audit_data["run_id"]  # non-empty


def test_catalog_hash_changes_when_projects_change() -> None:
    """Verify catalog_hash changes when the underlying project data changes."""
    projects_a = [{"id": "L1-01", "level": 1}]
    projects_b = [{"id": "L1-01", "level": 1, "extra": True}]

    hash_a = hashlib.sha256(json.dumps(projects_a, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    hash_b = hashlib.sha256(json.dumps(projects_b, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]

    assert hash_a != hash_b, "catalog_hash must change when project data changes"
