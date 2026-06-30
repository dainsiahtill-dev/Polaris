"""Architecture fence for environment-prefix normalization naming."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_env_prefix_normalization_does_not_use_compat_module_name() -> None:
    retired_path = POLARIS_ROOT / "_env_compat.py"
    canonical_path = POLARIS_ROOT / "env_prefix_normalization.py"

    assert not retired_path.exists(), "Retired _env_compat.py module was recreated."
    assert canonical_path.is_file(), "Environment prefix normalization must live in env_prefix_normalization.py."

    package_source = _read_text(POLARIS_ROOT / "__init__.py")
    server_source = _read_text(POLARIS_ROOT / "delivery/server.py")

    assert "polaris._env_compat" not in package_source
    assert "polaris._env_compat" not in server_source
    assert "polaris.env_prefix_normalization" in package_source
    assert "polaris.env_prefix_normalization" in server_source
