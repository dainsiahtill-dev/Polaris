from __future__ import annotations

from pathlib import Path

from polaris.infrastructure.accel.indexers.discovery import collect_source_files


def _write_source(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("print('polaris accel indexer')\n", encoding="utf-8")


def _relative_paths(paths: list[Path], root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in paths}


def test_auto_scope_expands_builtin_default_include_to_project_sources(tmp_path) -> None:
    _write_source(tmp_path / "src" / "app.py")
    _write_source(tmp_path / "docs" / "note.py")

    files = collect_source_files(
        tmp_path,
        {
            "index": {
                "include": ["src/**", "accel/**", "tests/**"],
                "scope_mode": "auto",
                "scan_timeout_seconds": 5,
            }
        },
    )

    assert _relative_paths(files, tmp_path) == {"docs/note.py", "src/app.py"}


def test_configured_scope_keeps_builtin_default_include_bounded(tmp_path) -> None:
    _write_source(tmp_path / "src" / "app.py")
    _write_source(tmp_path / "docs" / "note.py")

    files = collect_source_files(
        tmp_path,
        {
            "index": {
                "include": ["src/**", "accel/**", "tests/**"],
                "scope_mode": "configured",
                "scan_timeout_seconds": 5,
            }
        },
    )

    assert _relative_paths(files, tmp_path) == {"src/app.py"}
