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
