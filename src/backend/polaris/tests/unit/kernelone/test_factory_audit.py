"""Tests for factory-bench deterministic audit primitives."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from polaris.kernelone.benchmark import factory_audit
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

    def test_package_script_missing_local_entrypoint_fails_record(self, tmp_path: Path) -> None:
        ws = _project_workspace(tmp_path)
        (ws / "app.js").write_text("console.log('ok');\n", encoding="utf-8")
        (ws / "package.json").write_text(
            '{"scripts":{"start":"http-server .","test":"node test/check.js"}}\n',
            encoding="utf-8",
        )

        record = build_factory_audit_record(
            project={"id": "L2-08", "level": 2, "checks": ["html", "js_syntax", "min_files:2"]},
            workspace=str(ws),
        )

        assert record["all_checks_passed"] is False
        package_checks = [check for check in record["checks"] if check["check"] == "package_scripts"]
        assert package_checks
        assert package_checks[0]["ok"] is False
        assert "test/check.js" in package_checks[0]["detail"]
        assert all(check["ok"] for check in record["checks"] if check["check"] != "package_scripts")

    def test_package_script_validation_ignores_package_binaries(self, tmp_path: Path) -> None:
        ws = _project_workspace(tmp_path)
        (ws / "app.js").write_text("console.log('ok');\n", encoding="utf-8")
        (ws / "package.json").write_text(
            '{"scripts":{"dev":"vite --host 0.0.0.0","lint":"eslint src --max-warnings=0"}}\n',
            encoding="utf-8",
        )

        results = run_checks(str(ws), ["package_scripts"])

        assert results == [
            {
                "check": "package_scripts",
                "ok": True,
                "detail": "2 package scripts have valid local entrypoint references",
            }
        ]

    def test_package_script_validation_allows_node_builtin_test_mode(self, tmp_path: Path) -> None:
        ws = _project_workspace(tmp_path)
        (ws / "app.js").write_text("console.log('ok');\n", encoding="utf-8")
        (ws / "package.json").write_text(
            '{"scripts":{"test":"node --test"}}\n',
            encoding="utf-8",
        )

        results = run_checks(str(ws), ["package_scripts"])

        assert results[0]["ok"] is True

    def test_package_script_validation_rejects_echo_only_placeholders(self, tmp_path: Path) -> None:
        ws = _project_workspace(tmp_path)
        (ws / "scripts").mkdir()
        (ws / "scripts" / "verify.js").write_text("console.log('ok');\n", encoding="utf-8")
        (ws / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {
                        "build": "echo 'Building project...'",
                        "start": "echo 'Starting project...'",
                        "test": "node scripts/verify.js",
                    }
                }
            ),
            encoding="utf-8",
        )

        results = run_checks(str(ws), ["package_scripts"])

        assert results[0]["ok"] is False
        assert "script 'build' is a placeholder command" in results[0]["detail"]

    def test_package_script_validation_rejects_empty_scripts(self, tmp_path: Path) -> None:
        ws = _project_workspace(tmp_path)
        (ws / "package.json").write_text(json.dumps({"scripts": {}}), encoding="utf-8")

        results = run_checks(str(ws), ["package_scripts"])

        assert results[0]["ok"] is False
        assert "no scripts" in results[0]["detail"]

    def test_package_script_validation_allows_echo_before_real_command(self, tmp_path: Path) -> None:
        ws = _project_workspace(tmp_path)
        (ws / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {
                        "build": "echo building && vite build",
                    }
                }
            ),
            encoding="utf-8",
        )

        results = run_checks(str(ws), ["package_scripts"])

        assert results[0]["ok"] is True

    def test_package_script_validation_allows_build_before_dist_entrypoint(self, tmp_path: Path) -> None:
        ws = _project_workspace(tmp_path)
        (ws / "src").mkdir()
        (ws / "src" / "main.ts").write_text("console.log('ok');\n", encoding="utf-8")
        (ws / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {
                        "build": "tsc",
                        "start": "npm run build && node dist/main.js",
                    }
                }
            ),
            encoding="utf-8",
        )

        results = run_checks(str(ws), ["package_scripts"])

        assert results[0]["ok"] is True

    def test_package_script_missing_node_preload_fails_record(self, tmp_path: Path) -> None:
        ws = _project_workspace(tmp_path)
        (ws / "test").mkdir()
        (ws / "test" / "check.js").write_text("console.log('ok');\n", encoding="utf-8")
        (ws / "package.json").write_text(
            '{"scripts":{"test":"node -r ./test/setup.js test/check.js"}}\n',
            encoding="utf-8",
        )

        results = run_checks(str(ws), ["package_scripts"])

        assert results[0]["ok"] is False
        assert "./test/setup.js" in results[0]["detail"]

    def test_package_script_validation_allows_package_preload_subpath(self, tmp_path: Path) -> None:
        ws = _project_workspace(tmp_path)
        (ws / "test").mkdir()
        (ws / "test" / "check.js").write_text("console.log('ok');\n", encoding="utf-8")
        (ws / "package.json").write_text(
            '{"scripts":{"test":"node -r dotenv/config test/check.js"}}\n',
            encoding="utf-8",
        )

        results = run_checks(str(ws), ["package_scripts"])

        assert results[0]["ok"] is True

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


def test_js_syntax_accepts_inline_script_single_file_html(tmp_path) -> None:
    """L2-10 r4: a complete single-file HTML app (inline <script>) must not
    fail js_syntax just because no standalone .js exists."""
    from polaris.kernelone.benchmark.factory_audit import run_checks

    (tmp_path / "index.html").write_text(
        "<!DOCTYPE html>\n<html><body>\n"
        "<textarea id='editor'></textarea>\n"
        "<script>\nconst e = document.getElementById('editor');\nconsole.log(e);\n</script>\n"
        "</body></html>\n",
        encoding="utf-8",
    )
    results = run_checks(str(tmp_path), ["js_syntax"])
    assert results[0]["ok"] is True
    assert "inline <script>" in results[0]["detail"]


def test_js_syntax_still_fails_without_any_script(tmp_path) -> None:
    from polaris.kernelone.benchmark.factory_audit import run_checks

    (tmp_path / "index.html").write_text(
        "<!DOCTYPE html>\n<html><body><p>static only</p></body></html>\n",
        encoding="utf-8",
    )
    results = run_checks(str(tmp_path), ["js_syntax"])
    assert results[0]["ok"] is False


def test_js_syntax_empty_inline_script_not_enough(tmp_path) -> None:
    from polaris.kernelone.benchmark.factory_audit import run_checks

    (tmp_path / "index.html").write_text(
        "<html><body><script></script></body></html>\n",
        encoding="utf-8",
    )
    results = run_checks(str(tmp_path), ["js_syntax"])
    assert results[0]["ok"] is False


def test_runnable_any_accepts_web_shape(tmp_path) -> None:
    """L2-11 r1: shape-neutral briefs may be delivered as web apps."""
    from polaris.kernelone.benchmark.factory_audit import run_checks

    (tmp_path / "index.html").write_text(
        "<html><body><script>console.log('ok');</script></body></html>\n",
        encoding="utf-8",
    )
    results = run_checks(str(tmp_path), ["runnable_any"])
    assert results[0]["ok"] is True
    assert "web shape" in results[0]["detail"]


def test_runnable_any_accepts_python_shape(tmp_path) -> None:
    from polaris.kernelone.benchmark.factory_audit import run_checks

    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    results = run_checks(str(tmp_path), ["runnable_any"])
    assert results[0]["ok"] is True
    assert "python shape" in results[0]["detail"]


def test_runnable_any_fails_when_neither_shape(tmp_path) -> None:
    from polaris.kernelone.benchmark.factory_audit import run_checks

    (tmp_path / "notes.md").write_text("# doc only\n", encoding="utf-8")
    results = run_checks(str(tmp_path), ["runnable_any"])
    assert results[0]["ok"] is False


def test_runnable_any_broken_python_falls_through_to_web(tmp_path) -> None:
    from polaris.kernelone.benchmark.factory_audit import run_checks

    (tmp_path / "bad.py").write_text("def f(:\n", encoding="utf-8")
    (tmp_path / "index.html").write_text(
        "<html><body><script>console.log('ok');</script></body></html>\n",
        encoding="utf-8",
    )
    results = run_checks(str(tmp_path), ["runnable_any"])
    assert results[0]["ok"] is True
    assert "web shape" in results[0]["detail"]


def test_content_any_detects_hollow_scaffold(tmp_path) -> None:
    """L2-12 r1: a 43-line empty game loop passed every structural check while
    containing zero game features."""
    from polaris.kernelone.benchmark.factory_audit import run_checks

    (tmp_path / "game.js").write_text(
        "(function(){ function gameLoop(){} function init(){} })();\n",
        encoding="utf-8",
    )
    results = run_checks(str(tmp_path), ["content_any:paddle|ball|brick"])
    assert results[0]["ok"] is False
    assert "not found" in results[0]["detail"]


def test_content_any_passes_on_real_feature(tmp_path) -> None:
    from polaris.kernelone.benchmark.factory_audit import run_checks

    (tmp_path / "game.js").write_text(
        "const paddle = { x: 0, w: 80 };\nconst bricks = [];\n",
        encoding="utf-8",
    )
    results = run_checks(str(tmp_path), ["content_any:paddle|ball|brick"])
    assert results[0]["ok"] is True
    assert "game.js" in results[0]["detail"]


def test_content_any_bad_pattern_fails_closed(tmp_path) -> None:
    from polaris.kernelone.benchmark.factory_audit import run_checks

    (tmp_path / "a.js").write_text("x\n", encoding="utf-8")
    results = run_checks(str(tmp_path), ["content_any:[unclosed"])
    assert results[0]["ok"] is False
    assert "bad pattern" in results[0]["detail"]


def test_multilanguage_compile_checks_dispatch_to_toolchains(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "app.ts").write_text("export const answer: number = 42;\n", encoding="utf-8")
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    (tmp_path / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    (tmp_path / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / "Main.java").write_text(
        "public class Main { public static void main(String[] args) {} }\n", encoding="utf-8"
    )
    commands: list[list[str]] = []

    def fake_which(name: str) -> str:
        return f"/tool/{name}"

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(factory_audit.shutil, "which", fake_which)
    monkeypatch.setattr(factory_audit.subprocess, "run", fake_run)

    results = run_checks(str(tmp_path), ["ts_syntax", "go_compile", "rust_compile", "cpp_compile", "java_compile"])

    assert [item["ok"] for item in results] == [True, True, True, True, True]
    assert [Path(command[0]).name for command in commands] == ["tsc", "go", "rustc", "g++", "javac"]


def test_multilanguage_compile_check_reports_missing_toolchain(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    monkeypatch.setattr(factory_audit.shutil, "which", lambda _name: None)

    results = run_checks(str(tmp_path), ["go_compile"])

    assert results[0]["ok"] is False
    assert "go unavailable" in results[0]["detail"]
