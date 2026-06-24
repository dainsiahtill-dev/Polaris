"""Tests for platform package.json script quality checks."""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality import check_package_scripts


def test_check_package_scripts_rejects_placeholder_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"echo \\"No tests yet\\" && exit 0"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is False
    assert "placeholder" in result.detail


def test_check_package_scripts_rejects_missing_local_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"start":"node ./missing.js"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is False
    assert "missing local entrypoint" in result.detail


def test_check_package_scripts_accepts_build_script_before_dist_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"tsc","start":"npm run build && node dist/index.js"}}\n',
        encoding="utf-8",
    )

    result = check_package_scripts(str(tmp_path))

    assert result.ok is True
