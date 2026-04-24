from __future__ import annotations

import json
from pathlib import Path


def test_frontend_package_manifest_is_metadata_only() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    root_package = json.loads((repo_root / "package.json").read_text(encoding="utf-8"))
    frontend_package = json.loads((repo_root / "src" / "frontend" / "package.json").read_text(encoding="utf-8"))

    assert isinstance(root_package.get("dependencies"), dict)
    assert root_package["dependencies"]
    assert "dependencies" not in frontend_package
    assert "devDependencies" not in frontend_package
    assert frontend_package.get("type") == "module"
