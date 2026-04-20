"""Legacy Recipe Adapter - Backward Compatibility Adapter.

Converts legacy Recipe configuration to RoutingResult format.
Core principle: never pollute core engine; all conversion happens at entry point.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.kernelone.role.loaders import AnchorLoader, PersonaLoader, ProfessionLoader, RecipeConfig

from polaris.kernelone.role.routing.result import RoutingResult

logger = logging.getLogger(__name__)


class LegacyRecipeAdapter:
    """Legacy Recipe Adapter.

    Converts fixed Recipe to RoutingResult, maintaining backward compatibility.
    """

    def __init__(
        self,
        anchor_loader: AnchorLoader,
        persona_loader: PersonaLoader,
        profession_loader: ProfessionLoader,
    ) -> None:
        self._anchor_loader = anchor_loader
        self._persona_loader = persona_loader
        self._profession_loader = profession_loader

    def to_routing_result(self, recipe_id: str) -> RoutingResult | None:
        """将固定 Recipe 转换为 RoutingResult

        Args:
            recipe_id: Recipe ID (如 "senior_python_architect")

        Returns:
            RoutingResult 或 None (如果 Recipe 不存在)
        """
        # 尝试加载 Recipe
        from polaris.kernelone.role.loaders import get_recipe_loader

        recipe_loader = get_recipe_loader()

        recipe = recipe_loader.load(recipe_id)
        if not recipe:
            # 尝试 legacy ID
            recipe = recipe_loader.load_by_legacy_id(recipe_id)

        if not recipe:
            logger.warning(f"Recipe not found: {recipe_id}")
            return None

        # 加载对应的配置
        anchor = self._anchor_loader.load(recipe.anchor)
        profession = self._profession_loader.load(recipe.profession)
        persona = self._persona_loader.load(recipe.persona)

        if not anchor or not profession or not persona:
            logger.error(f"Failed to load recipe components: {recipe_id}")
            return None

        return RoutingResult(
            anchor_id=anchor.id,
            profession_id=profession.id,
            persona_id=persona.id,
            score=1.0,  # Full match, no degradation
            match_details={"legacy_recipe": True, "recipe_id": recipe_id},
            method="legacy_adapter",
        )

    def from_recipe_config(self, recipe: RecipeConfig) -> RoutingResult:
        """从 RecipeConfig 直接转换"""
        return RoutingResult(
            anchor_id=recipe.anchor,
            profession_id=recipe.profession,
            persona_id=recipe.persona,
            score=1.0,
            match_details={"legacy_recipe": True},
            method="legacy_adapter",
        )
