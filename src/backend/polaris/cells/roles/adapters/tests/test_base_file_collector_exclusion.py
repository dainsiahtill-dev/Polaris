"""Regression: base-file collectors must exclude vendored/generated dirs uniformly.

The 5 language base-file collectors (cpp/go/rust/java/java_test) must agree on
which directory subtrees to skip. Rust used a comprehensive ignore set
(``_RUST_BASE_FILE_IGNORES`` = .git/.venv/__pycache__/node_modules/target),
while cpp/go/java only checked ``_is_generated_build_path`` (build/cmake-build)
— so a ``.go``/``.cpp``/``.java`` file under ``.venv/`` or ``node_modules/``
would be wrongly collected as authored source and fed to the repair kernel.
This consolidates one shared skip predicate across all collectors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from polaris.cells.roles.adapters.internal.director.post_execution_repair_bridge import (
    _helpers,
)


@pytest.fixture
def workspace_with_vendored_go(tmp_path: Path) -> Path:
    """Workspace with a real .go file plus vendored/generated siblings."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.go").write_text("package main\n", encoding="utf-8")
    # vendored / dependency dirs that must NEVER be collected as authored source
    (ws / "node_modules" / "pkg").mkdir(parents=True)
    (ws / "node_modules" / "pkg" / "index.go").write_text("package pkg\n", encoding="utf-8")
    (ws / ".venv" / "lib").mkdir(parents=True)
    (ws / ".venv" / "lib" / "vendored.go").write_text("package vendored\n", encoding="utf-8")
    (ws / ".git").mkdir()
    (ws / ".git" / "hook.go").write_text("package git\n", encoding="utf-8")
    return ws


class TestCollectorVendoredExclusion:
    def test_go_collector_excludes_vendored_dirs(self, workspace_with_vendored_go: Path) -> None:
        collected = _helpers._collect_go_base_files(workspace_with_vendored_go)
        assert "main.go" in collected
        # vendored/generated must NOT leak in
        assert "node_modules/pkg/index.go" not in collected
        assert ".venv/lib/vendored.go" not in collected
        assert ".git/hook.go" not in collected

    def test_cpp_collector_excludes_vendored_dirs(self, tmp_path: Path) -> None:
        ws = tmp_path / "cppws"
        ws.mkdir()
        (ws / "main.cpp").write_text("int main(){}\n", encoding="utf-8")
        (ws / "node_modules" / "dep").mkdir(parents=True)
        (ws / "node_modules" / "dep" / "dep.cpp").write_text("x\n", encoding="utf-8")
        (ws / ".venv").mkdir()
        (ws / ".venv" / "v.cpp").write_text("y\n", encoding="utf-8")
        collected = _helpers._collect_cpp_base_files(ws)
        assert "main.cpp" in collected
        assert "node_modules/dep/dep.cpp" not in collected
        assert ".venv/v.cpp" not in collected

    def test_java_collector_excludes_vendored_dirs(self, tmp_path: Path) -> None:
        ws = tmp_path / "javaws"
        ws.mkdir()
        (ws / "Main.java").write_text("class Main {}\n", encoding="utf-8")
        (ws / "node_modules" / "dep").mkdir(parents=True)
        (ws / "node_modules" / "dep" / "Dep.java").write_text("class Dep {}\n", encoding="utf-8")
        collected = _helpers._collect_java_base_files(ws)
        assert "Main.java" in collected
        assert "node_modules/dep/Dep.java" not in collected

    def test_rust_collector_still_excludes_target(self, tmp_path: Path) -> None:
        """Rust regression: ``target/`` build output stays excluded after consolidation."""
        ws = tmp_path / "rustws"
        ws.mkdir()
        (ws / "Cargo.toml").write_text("[package]\nname=\"x\"\n", encoding="utf-8")
        (ws / "src").mkdir()
        (ws / "src" / "main.rs").write_text("fn main(){}\n", encoding="utf-8")
        (ws / "target" / "debug").mkdir(parents=True)
        (ws / "target" / "debug" / "built.rs").write_text("fn built(){}\n", encoding="utf-8")
        collected = _helpers._collect_rust_base_files(ws)
        assert "src/main.rs" in collected
        assert "target/debug/built.rs" not in collected

    def test_shared_skip_predicate_is_comprehensive(self) -> None:
        """The shared predicate covers all the dirs every language must skip."""
        pred = _helpers._is_vendored_or_generated_path
        for bad in ("node_modules", ".venv", ".git", "__pycache__", "target", "build", "cmake-build-debug"):
            assert pred(Path(f"proj/{bad}/x")), f"{bad} should be skipped"
        # authored source is never skipped
        assert not pred(Path("proj/src/main.go"))
        assert not pred(Path("proj/main.rs"))
