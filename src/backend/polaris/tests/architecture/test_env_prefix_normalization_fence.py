"""Architecture fence for environment-prefix normalization naming."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
POLARIS_ENV_TOKEN_RE = re.compile(r"(?<!VITE_)POLARIS_[A-Z0-9_]+")


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


def test_backend_product_code_reads_canonical_kernelone_env_prefix() -> None:
    """Only the boundary ACL may mention deprecated POLARIS_* env vars."""

    allowed_files = {
        POLARIS_ROOT / "env_prefix_normalization.py",
    }
    findings: list[str] = []

    for path in POLARIS_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if "tests" in path.parts:
            continue
        if path in allowed_files:
            continue
        text = _read_text(path)
        tokens = sorted(set(POLARIS_ENV_TOKEN_RE.findall(text)))
        if tokens:
            findings.append(f"{path.relative_to(BACKEND_ROOT)} contains deprecated env tokens: {tokens}")

    assert findings == []


def test_deprecated_polaris_env_prefix_maps_to_canonical_kernelone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Representative deprecated env vars must remain supported at the boundary."""

    from polaris.env_prefix_normalization import normalize_env_prefix

    for key in (
        "KERNELONE_INSTANCE_ID",
        "KERNELONE_FACTORY_LIVE_LLM_PREFLIGHT",
        "POLARIS_INSTANCE_ID",
        "POLARIS_FACTORY_LIVE_LLM_PREFLIGHT",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("POLARIS_INSTANCE_ID", "main")
    monkeypatch.setenv("POLARIS_FACTORY_LIVE_LLM_PREFLIGHT", "1")

    with pytest.warns(DeprecationWarning):
        normalize_env_prefix()

    assert os.environ["KERNELONE_INSTANCE_ID"] == "main"
    assert os.environ["KERNELONE_FACTORY_LIVE_LLM_PREFLIGHT"] == "1"
