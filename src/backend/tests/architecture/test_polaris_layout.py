from importlib import import_module
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
POLARIS_ROOT = BACKEND_ROOT / "polaris"


def test_polaris_root_directories_exist() -> None:
    expected = [
        "bootstrap",
        "delivery",
        "application",
        "domain",
        "kernelone",
        "infrastructure",
        "cells",
        "tests",
    ]

    assert POLARIS_ROOT.is_dir()
    for name in expected:
        path = POLARIS_ROOT / name
        assert path.is_dir(), f"Missing Polaris directory: {path}"
        assert (path / "__init__.py").is_file(), f"Missing package marker: {path / '__init__.py'}"


def test_polaris_packages_are_importable() -> None:
    modules = [
        "polaris",
        "polaris.bootstrap",
        "polaris.delivery",
        "polaris.application",
        "polaris.domain",
        "polaris.kernelone",
        "polaris.infrastructure",
        "polaris.cells",
    ]

    for module_name in modules:
        assert import_module(module_name) is not None
