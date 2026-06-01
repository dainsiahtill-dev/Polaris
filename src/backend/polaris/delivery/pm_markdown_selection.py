"""Ranking helpers for PM markdown planning inputs."""

from __future__ import annotations

import os
from pathlib import Path

_NOISE_TOKENS: tuple[str, ...] = (
    "audit",
    "diagnostic",
    "evidence",
    "health",
    "readiness",
    "report",
    "result",
    "status",
    "summary",
    "test",
    "trace",
    "验收",
    "审计",
    "状态",
    "证据",
)

_REQUIREMENTS_TOKENS: tuple[str, ...] = (
    "10_requirements",
    "prd",
    "product-requirements",
    "product_requirements",
    "requirements",
    "requirement",
    "spec",
    "产品需求",
    "产品文档",
    "需求",
    "需求文档",
    "项目文档",
)

_PLAN_TOKENS: tuple[str, ...] = (
    "implementation-plan",
    "implementation_plan",
    "plan",
    "planning",
    "roadmap",
    "落地计划",
    "计划",
)


def markdown_planning_score(path: str | Path, *, purpose: str = "generic") -> int:
    """Score a markdown artifact for PM planning-input selection.

    Newer files are not always better inputs. Status, audit, evidence, and
    diagnostic documents are frequently updated after a run, but PM should
    prefer product requirements and implementation plans as planning truth.
    """

    path_text = str(path or "")
    if not path_text:
        return 0

    normalized = path_text.replace("\\", "/").casefold()
    name = Path(path_text).name.casefold()
    score = 0

    if purpose == "requirements":
        for token in _REQUIREMENTS_TOKENS:
            if token.casefold() in name:
                score += 120
            elif token.casefold() in normalized:
                score += 60
        if "/docs/product/" in normalized:
            score += 80
        if name in {"requirements.md", "10_requirements.md"}:
            score += 120
        if "plan" in name or "计划" in name:
            score -= 40
        if "architecture" in name or "design" in name or "blueprint" in name:
            score -= 60
    elif purpose == "plan":
        for token in _PLAN_TOKENS:
            if token.casefold() in name:
                score += 120
            elif token.casefold() in normalized:
                score += 50
        if "/plans/" in normalized:
            score += 80
        if name in {"plan.md", "implementation-plan.md"}:
            score += 120
        if "requirements" in name or "需求" in name:
            score -= 35
    else:
        if "/docs/product/" in normalized or "/plans/" in normalized:
            score += 25

    for token in _NOISE_TOKENS:
        folded = token.casefold()
        if folded in name:
            score -= 140
        elif folded in normalized:
            score -= 60

    return score


def markdown_planning_sort_key(
    path: str | Path, *, mtime: float = 0.0, purpose: str = "generic"
) -> tuple[int, float, str]:
    """Return a stable sort key for markdown planning artifacts."""

    normalized = os.path.abspath(str(path or "")).casefold()
    return (markdown_planning_score(path, purpose=purpose), float(mtime or 0.0), normalized)


__all__ = ["markdown_planning_score", "markdown_planning_sort_key"]
