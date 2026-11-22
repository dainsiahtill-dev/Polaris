from __future__ import annotations

import json
from pathlib import Path

from polaris.cells.roles.runtime.internal.engineering_project_profile import (
    detect_project_profile,
    profile_fingerprint,
    prompt_additions,
)


def test_detects_web_project_from_package_json(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"name": "app"}), encoding="utf-8")
    profile = detect_project_profile(tmp_path)
    assert profile["schema_version"] == "project_profile.v1"
    assert profile["project_type"] == "web"
    assert "browser_observe" in profile["capabilities"]
    assert "browser_observe" in prompt_additions(profile)[0]["content"]


def test_detects_rust_project(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'", encoding="utf-8")
    assert detect_project_profile(tmp_path)["project_type"] == "rust"


def test_generic_project_has_no_capabilities(tmp_path: Path) -> None:
    profile = detect_project_profile(tmp_path)
    assert profile["project_type"] == "generic"
    assert profile["capabilities"] == []


def test_fingerprint_changes_with_type() -> None:
    one = {"project_type": "web", "signals": ["package.json"]}
    two = {"project_type": "rust", "signals": ["Cargo.toml"]}
    assert profile_fingerprint(one) != profile_fingerprint(two)


def test_binary_artifacts_are_signals(tmp_path: Path) -> None:
    binary = tmp_path / "target" / "release" / "app"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"\x7fELF")
    profile = detect_project_profile(tmp_path)
    assert "target/release/app" in profile["signals"]
