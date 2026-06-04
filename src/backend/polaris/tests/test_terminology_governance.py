from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
PRODUCTION_ROOT = PROJECT_ROOT / "src" / "backend" / "polaris"
FORBIDDEN_CODE_METAPHORS = (
    "认知生命体",
    "Cognitive Lifeform",
    "cognitive lifeform",
    "Cognitive Life Form",
    "cognitive life form",
)


def _is_production_python_file(path: Path) -> bool:
    parts = set(path.parts)
    return path.suffix == ".py" and "tests" not in parts and "generated" not in parts and "__pycache__" not in parts


def test_cognitive_lifeform_metaphor_stays_out_of_production_code_comments() -> None:
    violations: list[str] = []
    for path in PRODUCTION_ROOT.rglob("*.py"):
        if not _is_production_python_file(path):
            continue
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_CODE_METAPHORS:
            if token in text:
                violations.append(f"{path.relative_to(PROJECT_ROOT)} contains {token!r}")

    assert not violations, "\n".join(violations)
