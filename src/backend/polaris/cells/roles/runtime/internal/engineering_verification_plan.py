"""Host-derived fallback verification commands for an empty model selection.

Declared checks remain owned by the run verifier. This module only supplies a
small toolchain-aware default when the model provides no commands, and never
invents a toolchain that is absent on the host.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path


def _make_targets(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    targets: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)\s*:(?![=])", line)
        if match:
            targets.add(match.group(1))
    return targets


def default_verification_commands(workspace: Path) -> tuple[str, ...]:
    """Return a bounded available-toolchain plan, or an empty tuple if unknown."""

    if (workspace / "go.mod").is_file() and shutil.which("go"):
        return ("go build ./...", "go test ./...")
    manifest = workspace / "package.json"
    if manifest.is_file() and shutil.which("npm"):
        try:
            scripts = json.loads(manifest.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, json.JSONDecodeError, AttributeError):
            scripts = {}
        commands: list[str] = []
        if isinstance(scripts, dict):
            if isinstance(scripts.get("build"), str):
                commands.append("npm run build")
            if isinstance(scripts.get("test"), str):
                commands.append("npm test")
        if commands:
            return tuple(commands)
    if (workspace / "Cargo.toml").is_file() and shutil.which("cargo"):
        return ('CARGO_TARGET_DIR="$POLARIS_RUN_OUTPUT/cargo-target" cargo test',)
    if any((workspace / name).is_file() for name in ("pyproject.toml", "pytest.ini", "setup.cfg")):
        return ("python -m pytest -p no:cacheprovider",)
    targets = _make_targets(workspace / "Makefile")
    if "test" in targets and shutil.which("make"):
        return ("make test",)
    if "check" in targets and shutil.which("make"):
        return ("make check",)
    if (workspace / "CMakeLists.txt").is_file() and shutil.which("cmake") and shutil.which("ctest"):
        return (
            'cmake -S . -B "$POLARIS_RUN_OUTPUT/cmake-build" && cmake --build "$POLARIS_RUN_OUTPUT/cmake-build"',
            'ctest --test-dir "$POLARIS_RUN_OUTPUT/cmake-build"',
        )
    return ()
