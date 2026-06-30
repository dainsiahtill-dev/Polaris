"""Tests for _apply_deterministic_typescript_entrypoint_repair.

Covers:
- Missing src/index.ts when package.json main/start points to dist/index.js → generates
- Existing src/index.ts → does not overwrite
- No package.json → no-op
- package.json without dist reference → no-op
- build_real_run_gate passes with generated entrypoint
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.cells.roles.adapters.internal.director.deterministic_repairs.typescript_repairs import (
    _apply_deterministic_typescript_entrypoint_repair,
    _build_typescript_entrypoint_aggregator,
    _detect_typescript_entrypoint_from_package,
    _discover_src_modules,
)


def _make_adapter(tmp_path: Path) -> Any:
    from types import SimpleNamespace

    adapter = SimpleNamespace(workspace=str(tmp_path))
    adapter._update_task_progress = lambda *a, **kw: None  # type: ignore[attr-defined]
    adapter._execution = SimpleNamespace(_message_bus=None)  # type: ignore[attr-defined]
    return adapter


# ---------------------------------------------------------------------------
# _detect_typescript_entrypoint_from_package
# ---------------------------------------------------------------------------


class TestDetectTypescriptEntrypoint:
    def test_main_points_to_dist(self) -> None:
        assert _detect_typescript_entrypoint_from_package({"main": "dist/index.js"}) == "dist/index.js"

    def test_start_points_to_dist(self) -> None:
        pkg: dict[str, Any] = {"scripts": {"start": "node dist/index.js"}}
        assert _detect_typescript_entrypoint_from_package(pkg) == "dist/index.js"

    def test_main_points_to_build(self) -> None:
        assert _detect_typescript_entrypoint_from_package({"main": "build/main.js"}) == "build/main.js"

    def test_no_dist_reference(self) -> None:
        assert _detect_typescript_entrypoint_from_package({"main": "index.js"}) == ""

    def test_empty_package(self) -> None:
        assert _detect_typescript_entrypoint_from_package({}) == ""

    def test_start_without_dist(self) -> None:
        pkg: dict[str, Any] = {"scripts": {"start": "node server.js"}}
        assert _detect_typescript_entrypoint_from_package(pkg) == ""

    def test_main_points_to_out_dir(self) -> None:
        assert _detect_typescript_entrypoint_from_package({"main": "out/app.js"}) == "out/app.js"

    def test_main_points_to_bin_dir(self) -> None:
        assert _detect_typescript_entrypoint_from_package({"main": "bin/cli.js"}) == "bin/cli.js"


# ---------------------------------------------------------------------------
# _discover_src_modules
# ---------------------------------------------------------------------------


class TestDiscoverSrcModules:
    def test_finds_ts_files(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "flower.ts").write_text("export const x = 1;\n", encoding="utf-8")
        (src / "moon.ts").write_text("export const y = 2;\n", encoding="utf-8")
        modules = _discover_src_modules(src, tmp_path)
        assert modules == ["src/flower.ts", "src/moon.ts"]

    def test_excludes_index_ts(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "index.ts").write_text("export {};\n", encoding="utf-8")
        (src / "flower.ts").write_text("export const x = 1;\n", encoding="utf-8")
        modules = _discover_src_modules(src, tmp_path)
        assert modules == ["src/flower.ts"]

    def test_empty_src(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        assert _discover_src_modules(src, tmp_path) == []

    def test_nonexistent_src(self, tmp_path: Path) -> None:
        src = tmp_path / "nonexistent"
        assert _discover_src_modules(src, tmp_path) == []


# ---------------------------------------------------------------------------
# _build_typescript_entrypoint_aggregator
# ---------------------------------------------------------------------------


class TestBuildTypescriptEntrypointAggregator:
    def test_generates_imports_and_exports(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        content = _build_typescript_entrypoint_aggregator(["src/flower.ts", "src/moon.ts"], src, tmp_path)
        assert "import * as flower from './flower';" in content
        assert "import * as moon from './moon';" in content
        assert "export { flower };" in content
        assert "export { moon };" in content
        assert "console.log" not in content

    def test_empty_modules(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        content = _build_typescript_entrypoint_aggregator([], src, tmp_path)
        assert "export {};" in content
        assert "console.log" not in content

    def test_no_dependency_on_dom_or_node_lib(self, tmp_path: Path) -> None:
        """Generated content must not reference globals requiring DOM/Node lib."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "greeter.ts").write_text("export const greet = 'hi';\n", encoding="utf-8")
        content = _build_typescript_entrypoint_aggregator(["src/greeter.ts"], src, tmp_path)
        for forbidden in ("console", "window", "document", "process", "global", "Buffer"):
            assert forbidden not in content, f"Generated entrypoint must not reference '{forbidden}'"


# ---------------------------------------------------------------------------
# _apply_deterministic_typescript_entrypoint_repair
# ---------------------------------------------------------------------------


class TestEntrypointRepair:
    def test_generates_index_when_missing(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "flower.ts").write_text("export const hello = 'world';\n", encoding="utf-8")
        (src / "moon.ts").write_text("export const night = true;\n", encoding="utf-8")
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "name": "test-project",
                    "main": "dist/index.js",
                    "scripts": {"build": "tsc", "start": "node dist/index.js"},
                }
            ),
            encoding="utf-8",
        )

        results = _apply_deterministic_typescript_entrypoint_repair(
            _make_adapter(tmp_path),
            task_id="task-1",
            artifact_quality_errors=[],
        )

        assert len(results) == 1
        assert results[0]["result"]["source_tool"] == "deterministic_typescript_entrypoint_repair"
        assert results[0]["result"]["file"] == "src/index.ts"

        index_content = (src / "index.ts").read_text(encoding="utf-8")
        assert "import * as flower from './flower';" in index_content
        assert "import * as moon from './moon';" in index_content
        assert "export { flower };" in index_content
        assert "export { moon };" in index_content
        assert "console.log" not in index_content

    def test_does_not_overwrite_existing_index(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        existing_content = "// my custom index\nexport const custom = true;\n"
        (src / "index.ts").write_text(existing_content, encoding="utf-8")
        (src / "flower.ts").write_text("export const hello = 'world';\n", encoding="utf-8")
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "test", "main": "dist/index.js"}),
            encoding="utf-8",
        )

        results = _apply_deterministic_typescript_entrypoint_repair(
            _make_adapter(tmp_path),
            task_id="task-1",
            artifact_quality_errors=[],
        )

        assert results == []
        assert (src / "index.ts").read_text(encoding="utf-8") == existing_content

    def test_no_package_json(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "flower.ts").write_text("export const x = 1;\n", encoding="utf-8")

        results = _apply_deterministic_typescript_entrypoint_repair(
            _make_adapter(tmp_path),
            task_id="task-1",
            artifact_quality_errors=[],
        )

        assert results == []
        assert not (src / "index.ts").exists()

    def test_no_dist_reference(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "flower.ts").write_text("export const x = 1;\n", encoding="utf-8")
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "test", "main": "index.js"}),
            encoding="utf-8",
        )

        results = _apply_deterministic_typescript_entrypoint_repair(
            _make_adapter(tmp_path),
            task_id="task-1",
            artifact_quality_errors=[],
        )

        assert results == []
        assert not (src / "index.ts").exists()

    def test_no_src_directory(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "test", "main": "dist/index.js"}),
            encoding="utf-8",
        )

        results = _apply_deterministic_typescript_entrypoint_repair(
            _make_adapter(tmp_path),
            task_id="task-1",
            artifact_quality_errors=[],
        )

        assert results == []

    def test_generates_smoke_even_with_no_modules(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "test", "main": "dist/index.js"}),
            encoding="utf-8",
        )

        results = _apply_deterministic_typescript_entrypoint_repair(
            _make_adapter(tmp_path),
            task_id="task-1",
            artifact_quality_errors=[],
        )

        assert len(results) == 1
        index_content = (src / "index.ts").read_text(encoding="utf-8")
        assert "export {};" in index_content
        assert "console.log" not in index_content


# ---------------------------------------------------------------------------
# build_real_run_gate integration
# ---------------------------------------------------------------------------


class TestBuildRealRunGateEntrypoint:
    def test_generated_entrypoint_passes_tsc_check(self, tmp_path: Path) -> None:
        """Verify that build_real_run_gate can discover and validate the generated entrypoint."""

        src = tmp_path / "src"
        src.mkdir()
        (src / "flower.ts").write_text("export const hello = 'world';\n", encoding="utf-8")
        (src / "moon.ts").write_text("export const night = true;\n", encoding="utf-8")
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "name": "test-project",
                    "version": "1.0.0",
                    "main": "dist/index.js",
                    "scripts": {
                        "build": "tsc",
                        "start": "node dist/index.js",
                    },
                    "devDependencies": {"typescript": "^5.4.0"},
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "tsconfig.json").write_text(
            json.dumps(
                {
                    "compilerOptions": {
                        "outDir": "dist",
                        "rootDir": "src",
                        "target": "ES2020",
                        "module": "ES2020",
                        "moduleResolution": "node",
                        "esModuleInterop": True,
                        "strict": True,
                        "declaration": True,
                    },
                    "include": ["src/**/*.ts"],
                }
            ),
            encoding="utf-8",
        )

        # First generate the entrypoint
        repair_results = _apply_deterministic_typescript_entrypoint_repair(
            _make_adapter(tmp_path),
            task_id="task-1",
            artifact_quality_errors=[],
        )
        assert len(repair_results) == 1

        # Verify src/index.ts exists
        assert (src / "index.ts").exists()

        # The entrypoint should be discoverable
        index_content = (src / "index.ts").read_text(encoding="utf-8")
        assert "import" in index_content
        assert "export" in index_content


class TestEntrypointRepairNoConsoleDependency:
    """Verify generated entrypoint compiles without DOM/Node lib in tsconfig."""

    def test_generated_entrypoint_passes_tsc_without_dom_lib(self, tmp_path: Path) -> None:
        """tsc --noEmit must succeed when tsconfig has no DOM lib."""
        import subprocess

        src = tmp_path / "src"
        src.mkdir()
        (src / "greeter.ts").write_text("export const greet = 'hi';\n", encoding="utf-8")
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "name": "test-project",
                    "version": "1.0.0",
                    "main": "dist/index.js",
                    "scripts": {"build": "tsc"},
                }
            ),
            encoding="utf-8",
        )
        # tsconfig WITHOUT DOM or Node in lib
        (tmp_path / "tsconfig.json").write_text(
            json.dumps(
                {
                    "compilerOptions": {
                        "outDir": "dist",
                        "rootDir": "src",
                        "target": "ES2020",
                        "module": "ES2020",
                        "moduleResolution": "node",
                        "strict": True,
                        "declaration": True,
                        "lib": ["ES2020"],
                    },
                    "include": ["src/**/*.ts"],
                }
            ),
            encoding="utf-8",
        )

        results = _apply_deterministic_typescript_entrypoint_repair(
            _make_adapter(tmp_path),
            task_id="task-1",
            artifact_quality_errors=[],
        )
        assert len(results) == 1

        index_content = (src / "index.ts").read_text(encoding="utf-8")
        assert "console.log" not in index_content

        # Verify tsc --noEmit passes (tsc must be available)
        tsc = _which_tsc()
        if tsc:
            proc = subprocess.run(
                [tsc, "--noEmit", "--skipLibCheck", "--pretty", "false", "-p", "tsconfig.json"],
                cwd=str(tmp_path),
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert proc.returncode == 0, f"tsc --noEmit failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"

    def test_existing_dom_lib_not_broken_by_repair(self, tmp_path: Path) -> None:
        """When tsconfig already has DOM lib, entrypoint repair must not break it."""
        import subprocess

        src = tmp_path / "src"
        src.mkdir()
        (src / "ui.ts").write_text("export const el = 'div';\n", encoding="utf-8")
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "name": "test-project",
                    "version": "1.0.0",
                    "main": "dist/index.js",
                    "scripts": {"build": "tsc"},
                }
            ),
            encoding="utf-8",
        )
        # tsconfig WITH DOM lib
        (tmp_path / "tsconfig.json").write_text(
            json.dumps(
                {
                    "compilerOptions": {
                        "outDir": "dist",
                        "rootDir": "src",
                        "target": "ES2020",
                        "module": "ES2020",
                        "moduleResolution": "node",
                        "strict": True,
                        "lib": ["ES2020", "DOM"],
                    },
                    "include": ["src/**/*.ts"],
                }
            ),
            encoding="utf-8",
        )

        results = _apply_deterministic_typescript_entrypoint_repair(
            _make_adapter(tmp_path),
            task_id="task-1",
            artifact_quality_errors=[],
        )
        assert len(results) == 1

        # DOM lib must still be present
        tsconfig = json.loads((tmp_path / "tsconfig.json").read_text(encoding="utf-8"))
        assert "DOM" in tsconfig["compilerOptions"]["lib"]

        tsc = _which_tsc()
        if tsc:
            proc = subprocess.run(
                [tsc, "--noEmit", "--skipLibCheck", "--pretty", "false", "-p", "tsconfig.json"],
                cwd=str(tmp_path),
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert proc.returncode == 0, f"tsc --noEmit failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"

    def test_does_not_overwrite_existing_index_ts(self, tmp_path: Path) -> None:
        """Must not overwrite a pre-existing src/index.ts."""
        src = tmp_path / "src"
        src.mkdir()
        existing = "// my entry\nexport const mine = true;\n"
        (src / "index.ts").write_text(existing, encoding="utf-8")
        (src / "util.ts").write_text("export const x = 1;\n", encoding="utf-8")
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "test", "main": "dist/index.js"}),
            encoding="utf-8",
        )

        results = _apply_deterministic_typescript_entrypoint_repair(
            _make_adapter(tmp_path),
            task_id="task-1",
            artifact_quality_errors=[],
        )
        assert results == []
        assert (src / "index.ts").read_text(encoding="utf-8") == existing

    def test_real_run_gate_enters_npm_start_smoke(self, tmp_path: Path) -> None:
        """build_real_run_gate should enter npm start smoke with generated entrypoint."""
        from polaris.cells.factory.pipeline.public.service import build_real_run_gate

        src = tmp_path / "src"
        src.mkdir()
        (src / "app.ts").write_text("export const name = 'app';\n", encoding="utf-8")
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "name": "test-project",
                    "version": "1.0.0",
                    "main": "dist/index.js",
                    "scripts": {
                        "build": "tsc",
                        "start": "node dist/index.js",
                    },
                    "devDependencies": {"typescript": "^5.4.0"},
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "tsconfig.json").write_text(
            json.dumps(
                {
                    "compilerOptions": {
                        "outDir": "dist",
                        "rootDir": "src",
                        "target": "ES2020",
                        "module": "ES2020",
                        "moduleResolution": "node",
                        "strict": True,
                    },
                    "include": ["src/**/*.ts"],
                }
            ),
            encoding="utf-8",
        )

        # Generate entrypoint
        repair_results = _apply_deterministic_typescript_entrypoint_repair(
            _make_adapter(tmp_path),
            task_id="task-1",
            artifact_quality_errors=[],
        )
        assert len(repair_results) == 1
        assert (src / "index.ts").exists()

        record = {"code_files": ["src/index.ts", "src/app.ts", "package.json", "tsconfig.json"]}
        gate = build_real_run_gate(tmp_path, record, timeout_s=60)

        # build phase should exist (npm run build)
        build_cmds = [c for c in gate.get("commands", []) if c.get("phase") == "build_test_lint"]
        assert build_cmds, f"Expected build_test_lint commands, got: {gate.get('commands')}"

        # entrypoint phase should have been attempted
        entrypoint = gate.get("entrypoint", {})
        assert entrypoint.get("kind") in ("npm_start", ""), f"Expected entrypoint kind, got: {entrypoint}"


def _which_tsc() -> str:
    """Find tsc binary path, or empty string if not available."""
    import shutil

    return shutil.which("tsc") or ""
