from __future__ import annotations

from pathlib import Path

from polaris.cells.runtime.artifact_store.internal.artifacts import _load_goals


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_load_goals_prefers_product_requirements_over_retired_overview(tmp_path: Path) -> None:
    workspace = tmp_path
    _write_text(
        workspace / "docs" / "00_overview.md",
        "# Overview\n\n## Goal\n- retired overview goal\n",
    )
    _write_text(
        workspace / "docs" / "product" / "requirements.md",
        "# Product Requirements\n\n## Goal\n- canonical product goal\n",
    )

    assert _load_goals(str(workspace)) == ["canonical product goal"]


def test_load_goals_keeps_retired_overview_as_read_only_fallback(tmp_path: Path) -> None:
    workspace = tmp_path
    _write_text(
        workspace / "docs" / "00_overview.md",
        "# Overview\n\n## Goal\n- imported retired overview goal\n",
    )

    assert _load_goals(str(workspace)) == ["imported retired overview goal"]
