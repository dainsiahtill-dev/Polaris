"""Routing Result and Related Data Structures."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RoleTriple:
    """角色三元组 (Anchor + Profession + Persona)"""

    anchor_id: str
    profession_id: str
    persona_id: str

    def __str__(self) -> str:
        return f"{self.anchor_id} + {self.profession_id} + {self.persona_id}"


@dataclass
class ScoringResult:
    """评分结果"""

    total_score: float
    details: dict[str, float] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"ScoringResult(score={self.total_score:.3f})"


@dataclass
class RoutingResult:
    """路由结果"""

    anchor_id: str
    profession_id: str
    persona_id: str
    score: float  # 综合评分 0.0 - 1.0
    match_details: dict[str, Any] = field(default_factory=dict)
    fallback_count: int = 0
    confidence: float = 1.0
    method: str = "rule_based"
    warnings: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def role_triple(self) -> RoleTriple:
        return RoleTriple(
            anchor_id=self.anchor_id,
            profession_id=self.profession_id,
            persona_id=self.persona_id,
        )


@dataclass
class ResolvedTriple:
    """解决冲突后的最终三元组"""

    anchor_id: str
    profession_id: str
    persona_id: str
    resolution: str  # "inferred_only" | "manual_preferred" | "persona_relaxed"
    warnings: list[str] = field(default_factory=list)


@dataclass
class RoutingManualSpec:
    """用户显式指定的路由规格"""

    anchor_id: str | None = None
    profession_id: str | None = None
    persona_id: str | None = None


@dataclass
class RoutingInference:
    """系统推断的路由结果"""

    anchor_id: str
    profession_id: str
    persona_id: str
    confidence: float
