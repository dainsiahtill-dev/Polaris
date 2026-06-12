"""Tests for factory-bench deterministic audit primitives."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.benchmark.factory_audit import (
    FACTORY_AUDIT_SCHEMA_VERSION,
    aggregate_factory_audits,
    build_factory_audit_record,
    collect_workspace_inventory,
    run_checks,
)


def _project_workspace(tmp_path: Path, *, broken_py: bool = False) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "calculator.py").write_text(
        "def add(a, b):\n    return a + b\n" if not broken_py else "def add(a, b:\n    return\n",
        encoding="utf-8",
    )
    (tmp_path / "index.html").write_text("<html><body>hi</body></html>\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# project\n", encoding="utf-8")
    polaris_dir = tmp_path / ".polaris" / "runtime"
    polaris_dir.mkdir(parents=True)
    (polaris_dir / "noise.json").write_text("{}", encoding="utf-8")
    return tmp_path


class TestInventoryAndChecks:
    def test_inventory_excludes_runtime_dirs(self, tmp_path: Path) -> None:
        ws = _project_workspace(tmp_path)
        inv = collect_workspace_inventory(str(ws))
        assert "calculator.py" in inv["code_files"]
        assert "index.html" in inv["code_files"]
        assert "README.md" in inv["doc_files"]
        assert not any(".polaris" in f for f in inv["code_files"])

    def test_py_compile_pass_and_fail(self, tmp_path: Path) -> None:
        ok_ws = _project_workspace(tmp_path / "ok")
        results = run_checks(str(ok_ws), ["py_compile"])
        assert results[0]["ok"] is True

        bad_ws = _project_workspace(tmp_path / "bad", broken_py=True)
        results = run_checks(str(bad_ws), ["py_compile"])
        assert results[0]["ok"] is False
        assert "fail to compile" in results[0]["detail"]

    def test_html_and_min_files(self, tmp_path: Path) -> None:
        ws = _project_workspace(tmp_path)
        results = run_checks(str(ws), ["html", "min_files:2", "min_files:99"])
        assert results[0]["ok"] is True
        assert results[1]["ok"] is True
        assert results[2]["ok"] is False

    def test_unknown_check_fails_closed(self, tmp_path: Path) -> None:
        ws = _project_workspace(tmp_path)
        results = run_checks(str(ws), ["teleport"])
        assert results[0]["ok"] is False
        assert "unknown check" in results[0]["detail"]

    def test_empty_workspace_py_compile_fails(self, tmp_path: Path) -> None:
        results = run_checks(str(tmp_path), ["py_compile"])
        assert results[0]["ok"] is False


class TestAuditRecord:
    def test_record_assembly(self, tmp_path: Path) -> None:
        ws = _project_workspace(tmp_path)
        project = {"id": "L1-01", "level": 1, "domain": "software", "title": "calc", "checks": ["py_compile", "html"]}
        record = build_factory_audit_record(
            project=project,
            workspace=str(ws),
            artifact_globs={"plan": ["docs/plan.md"], "blueprint": [], "verdict": []},
        )
        assert record["schema_version"] == FACTORY_AUDIT_SCHEMA_VERSION
        assert record["project_id"] == "L1-01"
        assert record["all_checks_passed"] is True
        assert record["has_plan_doc"] is True
        assert record["has_blueprint_doc"] is False

    def test_no_checks_means_not_passed(self, tmp_path: Path) -> None:
        record = build_factory_audit_record(project={"id": "x", "level": 1, "checks": []}, workspace=str(tmp_path))
        assert record["all_checks_passed"] is False

    def test_aggregate_by_level(self, tmp_path: Path) -> None:
        ws = _project_workspace(tmp_path)
        good = build_factory_audit_record(project={"id": "L1-01", "level": 1, "checks": ["html"]}, workspace=str(ws))
        bad = build_factory_audit_record(
            project={"id": "L2-07", "level": 2, "checks": ["min_files:99"]}, workspace=str(ws)
        )
        agg = aggregate_factory_audits([good, bad])
        assert agg["total"] == 2
        assert agg["all_checks_passed"] == 1
        assert agg["by_level"]["L1"]["passed"] == 1
        assert agg["by_level"]["L2"]["passed"] == 0
