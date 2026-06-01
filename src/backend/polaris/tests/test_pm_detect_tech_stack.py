from __future__ import annotations

import os
import sys

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for candidate in (BACKEND_ROOT,):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from polaris.delivery.cli.pm.orchestration_core import detect_tech_stack  # noqa: E402


def test_detect_tech_stack_rust_not_confused_by_architecture_word() -> None:
    requirements = """
    ## Goal
    Build a Rust RSS reader backend.
    README includes run/test instructions and architecture notes.
    React dashboard is explicitly out of scope for this backend-only round.
    Files: src/main.rs, src/lib.rs, Cargo.toml
    """
    detected = detect_tech_stack(requirements, "")
    assert detected.get("language") == "rust"
    assert detected.get("framework") is None


def test_detect_tech_stack_typescript_react_when_explicit() -> None:
    requirements = """
    Build a TypeScript web API with React dashboard.
    Use tsconfig.json and src/index.ts as entry points.
    """
    detected = detect_tech_stack(requirements, "")
    assert detected.get("language") == "typescript"
    assert detected.get("framework") == "react"


def test_detect_tech_stack_fashion_desktop_workbench_prefers_typescript() -> None:
    requirements = """
    Build FashionGen Studio as a desktop creative production tool.
    Use TypeScript, React, Electron, and TailwindCSS for the frontend.
    Use a Python orchestration backend for image generation providers.
    The core UI is a model generation workbench, face lab, scene workbench,
    and batch production workspace.
    """
    detected = detect_tech_stack(requirements, "")

    assert detected.get("language") == "typescript"
    assert detected.get("framework") == "react"
    assert detected.get("project_type") == "desktop"
    assert "python" in detected.get("alternative_languages", [])
