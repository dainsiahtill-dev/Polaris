"""Architecture fence for the retired backend root server shim."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = BACKEND_ROOT.parents[1]
ROOT_SERVER_SHIM = BACKEND_ROOT / "server.py"
CANONICAL_SERVER = BACKEND_ROOT / "polaris" / "delivery" / "server.py"
LAUNCHER_FILES = (
    REPO_ROOT / "src" / "electron" / "main.cjs",
    REPO_ROOT / "infrastructure" / "scripts" / "run-electron.js",
    REPO_ROOT / "infrastructure" / "scripts" / "run-web.js",
    BACKEND_ROOT / "polaris" / "tests" / "electron" / "webFixtures.ts",
)


def test_backend_root_server_shim_is_retired() -> None:
    """The backend must start through the canonical delivery module."""
    assert not ROOT_SERVER_SHIM.exists()
    assert CANONICAL_SERVER.is_file()


def test_launchers_use_delivery_server_module() -> None:
    """Product and test launchers must not depend on the retired root file."""
    offenders: list[str] = []
    for path in LAUNCHER_FILES:
        source = path.read_text(encoding="utf-8")
        if "server.py" in source or "src/backend/server.py" in source:
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
        assert "polaris.delivery.server" in source, f"{path} must use the canonical backend module"

    assert offenders == []
