"""Regression tests for PM Director compatibility routing."""

from __future__ import annotations

from pathlib import Path

from polaris.delivery.cli.pm.director_interface_core import (
    CanonicalDirectorAdapter,
    DirectorFactory,
    create_director,
)


def test_script_director_type_aliases_to_canonical_adapter(tmp_path: Path) -> None:
    """Old script mode is accepted only as a config alias."""

    director = DirectorFactory.create("script", tmp_path)
    assert isinstance(director, CanonicalDirectorAdapter)
    assert director.get_info()["adapter"] == "roles.adapters.director"


def test_auto_director_type_uses_canonical_adapter(tmp_path: Path) -> None:
    """Auto mode must not inspect or spawn local Director scripts."""

    script_path = tmp_path / "src" / "backend" / "scripts" / "loop-director.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("raise SystemExit('must not run')\n", encoding="utf-8")

    director = create_director(str(tmp_path), director_type="auto")
    assert isinstance(director, CanonicalDirectorAdapter)
