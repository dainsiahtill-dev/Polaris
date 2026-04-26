from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from polaris.cells.llm.evaluation.internal.baseline_library import (
    list_baseline_library_sources,
    pull_baseline_library,
)
from polaris.kernelone.storage import resolve_runtime_path


def _local_tmp_dir(label: str) -> Path:
    path = Path("tmp_pytest_agentic_eval_local") / f"{label}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_list_baseline_library_sources_contains_public_catalog() -> None:
    catalog = list_baseline_library_sources()
    assert "bfcl" in catalog
    assert "toolbench" in catalog
    assert "files" in catalog["bfcl"]
    assert len(list(catalog["bfcl"]["files"])) >= 1


def test_pull_baseline_library_writes_manifest_and_files() -> None:
    workspace = _local_tmp_dir("baseline-pull")
    output_root = Path(resolve_runtime_path(str(workspace), "runtime/llm_evaluations/baselines"))

    def _fake_fetch(url: str, timeout_seconds: float) -> str:
        return f"url={url}\ntimeout={timeout_seconds}\n"

    payload = pull_baseline_library(
        workspace=str(workspace),
        sources=["bfcl"],
        output_root=str(output_root),
        timeout_seconds=7.0,
        fetch_text=_fake_fetch,
    )

    assert payload["ok"] is True
    assert payload["selected_sources"] == ["bfcl"]
    assert payload["unknown_sources"] == []
    assert len(payload["source_results"]) == 1
    source = payload["source_results"][0]
    assert source["source"] == "bfcl"
    assert source["failed_count"] == 0
    assert source["downloaded_count"] >= 1
    manifest_path = Path(str(payload["manifest_path"]))
    assert manifest_path.is_file()
    first_download = source["downloaded_files"][0]
    downloaded_path = Path(str(first_download["absolute_path"]))
    assert downloaded_path.is_file()
    content = downloaded_path.read_text(encoding="utf-8")
    assert "url=" in content


def test_pull_baseline_library_reports_unknown_sources() -> None:
    workspace = _local_tmp_dir("baseline-unknown")
    payload = pull_baseline_library(
        workspace=str(workspace),
        sources=["not-exist"],
        output_root="runtime/llm_evaluations/baselines",
        timeout_seconds=5.0,
        fetch_text=lambda _url, _timeout: "ok",
    )

    assert payload["ok"] is False
    assert payload["selected_sources"] == []
    assert payload["unknown_sources"] == ["not-exist"]
    assert Path(str(payload["manifest_path"])).is_file()


def test_pull_baseline_library_uses_cache_without_network_refetch() -> None:
    workspace = _local_tmp_dir("baseline-cache-hit")
    output_root = Path(resolve_runtime_path(str(workspace), "runtime/llm_evaluations/baselines"))
    fetch_count = {"calls": 0}

    def _fake_fetch(url: str, timeout_seconds: float) -> str:
        fetch_count["calls"] += 1
        return f"cached-url={url}\ntimeout={timeout_seconds}\n"

    first_payload = pull_baseline_library(
        workspace=str(workspace),
        sources=["bfcl"],
        output_root=str(output_root),
        timeout_seconds=5.0,
        fetch_text=_fake_fetch,
    )
    assert first_payload["ok"] is True
    assert fetch_count["calls"] >= 1

    def _network_should_not_run(_url: str, _timeout: float) -> str:
        raise AssertionError("network fetch should be skipped on cache hit")

    second_payload = pull_baseline_library(
        workspace=str(workspace),
        sources=["bfcl"],
        output_root=str(output_root),
        timeout_seconds=5.0,
        fetch_text=_network_should_not_run,
    )
    assert second_payload["ok"] is True
    second_source = second_payload["source_results"][0]
    assert int(second_source["cache_hits"]) >= 1
    assert int(second_source["network_downloads"]) == 0


def test_pull_baseline_library_check_only_reports_cache_miss() -> None:
    workspace = _local_tmp_dir("baseline-cache-check-miss")
    output_root = Path(resolve_runtime_path(str(workspace), "runtime/llm_evaluations/baselines"))

    payload = pull_baseline_library(
        workspace=str(workspace),
        sources=["bfcl"],
        output_root=str(output_root),
        timeout_seconds=5.0,
        check_only=True,
        fetch_text=lambda _url, _timeout: "should-not-be-used",
    )

    assert payload["ok"] is False
    assert payload["check_only"] is True
    source = payload["source_results"][0]
    assert source["status"] == "cache_miss"
    assert int(source["failed_count"]) >= 1
    assert any(str(item.get("error")) == "cache_miss" for item in source["failed_files"])
