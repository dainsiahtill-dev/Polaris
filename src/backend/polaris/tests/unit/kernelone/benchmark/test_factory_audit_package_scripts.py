from __future__ import annotations

import json
from pathlib import Path

from polaris.kernelone.benchmark.factory_audit import _check_package_scripts


def test_package_scripts_allow_prestart_build_before_dist_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "factory-bench-l1-01",
                "version": "1.0.0",
                "scripts": {
                    "build": "tsc",
                    "prestart": "npm run build",
                    "start": "node dist/index.js",
                    "test": "node scripts/test.mjs",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    test_script = tmp_path / "scripts" / "test.mjs"
    test_script.parent.mkdir(parents=True, exist_ok=True)
    test_script.write_text("console.log('ok');\n", encoding="utf-8")

    ok, detail = _check_package_scripts(str(tmp_path))

    assert ok is True
    assert "missing local entrypoint" not in detail


def test_package_scripts_allow_start_dist_entrypoint_when_build_script_exists(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "factory-bench-l1-01",
                "version": "1.0.0",
                "scripts": {
                    "build": "tsc",
                    "start": "node dist/index.js",
                    "test": "tsc && node dist/index.js",
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    ok, detail = _check_package_scripts(str(tmp_path))

    assert ok is True
    assert "missing local entrypoint" not in detail


def test_package_scripts_reject_echo_only_build_and_start(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "echo 'Building project...'",
                    "start": "echo 'Starting project...'",
                }
            }
        ),
        encoding="utf-8",
    )

    ok, detail = _check_package_scripts(str(tmp_path))

    assert ok is False
    assert "placeholder" in detail


def test_package_scripts_allow_echo_before_real_command(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "echo building && tsc",
                }
            }
        ),
        encoding="utf-8",
    )

    ok, _detail = _check_package_scripts(str(tmp_path))

    assert ok is True


def test_package_scripts_reject_empty_scripts_dict(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {}}), encoding="utf-8")

    ok, detail = _check_package_scripts(str(tmp_path))

    assert ok is False
    assert "no scripts" in detail


def test_package_scripts_missing_package_json(tmp_path: Path) -> None:
    ok, detail = _check_package_scripts(str(tmp_path))

    assert ok is False
    assert "not found" in detail
