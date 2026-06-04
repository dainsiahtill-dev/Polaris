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
CURRENT_STATUS_DOCS = (
    Path("docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md"),
    Path("docs/blueprints/TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_CLOSURE_MATRIX_20260416.md"),
    Path("docs/blueprints/STREAM_SHADOW_ENGINE_RELIABILITY_ASSESSMENT_20260417.md"),
    Path("src/backend/docs/AGENT_COLLABORATION_EDA_TASK_MARKET_BLUEPRINT_2026-04-14.md"),
    Path(
        "src/backend/docs/governance/decisions/adr-0071-transaction-kernel-single-commit-and-context-plane-isolation.md"
    ),
    Path("src/backend/docs/governance/decisions/adr-0077-speculative-execution-kernel-v2.md"),
    Path("src/backend/docs/governance/templates/verification-cards/vc-20260417-speculative-execution-kernel-v2.yaml"),
)
SUPERSEDED_STATUS_TOKENS = (
    "shadow-sidecar",
    "默认关闭",
    "骨架实现",
    "骨架级",
)
CURRENT_STATUS_QUALIFIERS = (
    "2026-06-04",
    "覆盖",
    "基线",
    "立项时",
    "不再代表当前",
    "superseded",
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


def test_cognitive_runtime_current_status_docs_qualify_superseded_claims() -> None:
    violations: list[str] = []
    for relative_path in CURRENT_STATUS_DOCS:
        path = PROJECT_ROOT / relative_path
        lines = path.read_text(encoding="utf-8").splitlines()
        for line_index, line in enumerate(lines):
            token = next((item for item in SUPERSEDED_STATUS_TOKENS if item in line), "")
            if not token:
                continue
            start = max(0, line_index - 8)
            end = min(len(lines), line_index + 9)
            nearby_text = "\n".join(lines[start:end])
            if not all(
                (
                    "2026-06-04" in nearby_text,
                    any(qualifier in nearby_text for qualifier in CURRENT_STATUS_QUALIFIERS),
                )
            ):
                violations.append(f"{relative_path}:{line_index + 1} contains unqualified {token!r}")

    assert not violations, "\n".join(violations)
