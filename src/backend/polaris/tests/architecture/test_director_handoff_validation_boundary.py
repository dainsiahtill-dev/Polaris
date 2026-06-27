"""Architecture fence for Director handoff validation ownership."""

from __future__ import annotations

from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"

LEGACY_EVALUATOR_NAME = "evaluate_handoff_decision_for_blueprint"
SHARED_VALIDATOR_NAME = "validate_director_handoff_from_payload"

ALLOWED_LEGACY_EVALUATOR_PATHS = {
    BACKEND_ROOT / "polaris" / "cells" / "chief_engineer" / "blueprint" / "public" / "__init__.py",
    BACKEND_ROOT / "polaris" / "cells" / "chief_engineer" / "blueprint" / "public" / "service.py",
    BACKEND_ROOT / "polaris" / "delivery" / "http" / "v2" / "chief_engineer.py",
}


def _production_python_files() -> list[Path]:
    return [
        path
        for path in sorted(POLARIS_ROOT.rglob("*.py"))
        if "__pycache__" not in path.parts
        and "generated" not in path.parts
        and "tests" not in path.parts
        and not path.name.startswith("test_")
    ]


def test_director_dispatch_paths_use_shared_handoff_validation() -> None:
    offenders: list[str] = []
    for path in _production_python_files():
        if path in ALLOWED_LEGACY_EVALUATOR_PATHS:
            continue
        text = path.read_text(encoding="utf-8")
        if LEGACY_EVALUATOR_NAME in text:
            offenders.append(str(path.relative_to(BACKEND_ROOT)))

    assert offenders == [], (
        "Director-dispatching code must call "
        f"`{SHARED_VALIDATOR_NAME}` instead of directly calling "
        f"`{LEGACY_EVALUATOR_NAME}`. Offenders: {offenders}"
    )
