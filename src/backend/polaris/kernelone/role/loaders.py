"""Role Configuration Loaders.

Loaders for Anchor, Persona, Profession, and Recipe configurations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Asset directory
_ASSETS_DIR = Path(__file__).parent.parent.parent / "assets" / "roles"


@dataclass
class AnchorConfig:
    """System Anchor configuration."""

    id: str
    name: str
    version: str
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    macro_workflow: dict[str, Any] = field(default_factory=dict)
    output_constraint: dict[str, Any] = field(default_factory=dict)
    tool_whitelist: dict[str, list[str]] = field(default_factory=dict)
    inter_anchor_protocol: dict[str, Any] = field(default_factory=dict)


@dataclass
class PersonaConfig:
    """Persona configuration."""

    id: str
    name: str
    version: str
    traits: str
    tone: str
    vocabulary: list[str]
    expression: dict[str, str] = field(default_factory=dict)
    description: str = ""
    compatible_anchors: list[str] = field(default_factory=list)
    compatible_professions: list[str] = field(default_factory=list)


@dataclass
class ProfessionConfig:
    """Profession configuration."""

    id: str
    name: str
    version: str
    identity: str
    expertise: list[str]
    description: str = ""
    workflow: dict[str, Any] = field(default_factory=dict)
    engineering_standards: dict[str, Any] = field(default_factory=dict)
    task_protocols: dict[str, Any] = field(default_factory=dict)
    output_format: dict[str, Any] = field(default_factory=dict)
    provider: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecipeConfig:
    """Role Recipe configuration (Anchor + Persona + Profession)."""

    anchor: str
    persona: str
    profession: str
    domain: str | None = None
    description: str = ""
    version: str = "1.0"
    backward_compatible: bool = False
    legacy_id: str | None = None


class AnchorLoader:
    """Loads and caches Anchor configurations."""

    def __init__(self) -> None:
        self._cache: dict[str, AnchorConfig] = {}

    def load(self, anchor_id: str) -> AnchorConfig | None:
        """Load an Anchor configuration by ID."""
        if anchor_id in self._cache:
            return self._cache[anchor_id]

        file_path = _ASSETS_DIR / "anchors" / f"{anchor_id}.yaml"
        if not file_path.exists():
            logger.warning(f"Anchor not found: {anchor_id}")
            return None

        try:
            with open(file_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            config = AnchorConfig(
                id=data["id"],
                name=data["name"],
                version=data["version"],
                description=data.get("description", ""),
                capabilities=data.get("capabilities", []),
                macro_workflow=data.get("macro_workflow", {}),
                output_constraint=data.get("output_constraint", {}),
                tool_whitelist=data.get("tool_whitelist", {}),
                inter_anchor_protocol=data.get("inter_anchor_protocol", {}),
            )

            self._cache[anchor_id] = config
            return config

        except (yaml.YAMLError, OSError, KeyError) as e:
            logger.error(f"Failed to load Anchor {anchor_id}: {e}")
            return None

    def get_workflow(self, anchor_id: str) -> dict[str, Any] | None:
        """Get the workflow definition for an Anchor."""
        anchor = self.load(anchor_id)
        return anchor.macro_workflow if anchor else None


class PersonaLoader:
    """Loads and caches Persona configurations."""

    def __init__(self) -> None:
        self._cache: dict[str, PersonaConfig] = {}

    def load(self, persona_id: str) -> PersonaConfig | None:
        """Load a Persona configuration by ID."""
        if persona_id in self._cache:
            return self._cache[persona_id]

        file_path = _ASSETS_DIR / "personas" / f"{persona_id}.yaml"
        if not file_path.exists():
            logger.warning(f"Persona not found: {persona_id}")
            return None

        try:
            with open(file_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            config = PersonaConfig(
                id=data["id"],
                name=data["name"],
                version=data["version"],
                description=data.get("description", ""),
                traits=data["traits"],
                tone=data["tone"],
                vocabulary=data["vocabulary"],
                expression=data.get("expression", {}),
                compatible_anchors=data.get("compatible_anchors", []),
                compatible_professions=data.get("compatible_professions", []),
            )

            self._cache[persona_id] = config
            return config

        except (yaml.YAMLError, OSError, KeyError) as e:
            logger.error(f"Failed to load Persona {persona_id}: {e}")
            return None


class ProfessionLoader:
    """Loads and caches Profession configurations."""

    def __init__(self) -> None:
        self._cache: dict[str, ProfessionConfig] = {}

    def load(self, profession_id: str) -> ProfessionConfig | None:
        """Load a Profession configuration by ID."""
        if profession_id in self._cache:
            return self._cache[profession_id]

        # Skip base template
        if profession_id.startswith("_"):
            return None

        file_path = _ASSETS_DIR / "professions" / f"{profession_id}.yaml"
        if not file_path.exists():
            logger.warning(f"Profession not found: {profession_id}")
            return None

        try:
            with open(file_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            # Handle parent inheritance
            parent_id = data.get("parent")
            parent_data: dict[str, Any] = {}
            if parent_id:
                parent_path = _ASSETS_DIR / "professions" / f"{parent_id}.yaml"
                if parent_path.exists():
                    try:
                        with open(parent_path, encoding="utf-8") as pf:
                            parent_data = yaml.safe_load(pf) or {}
                    except yaml.YAMLError as e:
                        logger.error(f"Failed to parse parent profession {parent_id}: {e}")
                        raise

            # Merge parent data (child overrides parent)
            merged = self._merge_configs(parent_data, data)

            config = ProfessionConfig(
                id=merged["id"],
                name=merged["name"],
                version=merged["version"],
                description=merged.get("description", ""),
                identity=merged["identity"],
                expertise=merged["expertise"],
                workflow=merged.get("workflow", {}),
                engineering_standards=merged.get("engineering_standards", {}),
                task_protocols=merged.get("task_protocols", {}),
                output_format=merged.get("output_format", {}),
                provider=merged.get("provider", {}),
            )

            self._cache[profession_id] = config
            return config

        except (yaml.YAMLError, OSError, KeyError) as e:
            logger.error(f"Failed to load Profession {profession_id}: {e}")
            return None

    def _merge_configs(self, parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
        """Merge child config into parent config."""
        import copy

        result = copy.deepcopy(parent)

        for key, value in child.items():
            if key == "parent":
                continue
            if isinstance(value, dict) and key in result and isinstance(result[key], dict):
                result[key] = self._merge_configs(result[key], value)
            elif isinstance(value, list) and key in result and isinstance(result[key], list):
                # Lists are replaced, not extended
                result[key] = value
            else:
                result[key] = value

        return result


class RecipeLoader:
    """Loads and caches Recipe configurations."""

    def __init__(self) -> None:
        self._cache: dict[str, RecipeConfig] = {}

    def load(self, recipe_id: str) -> RecipeConfig | None:
        """Load a Recipe configuration by ID."""
        if recipe_id in self._cache:
            return self._cache[recipe_id]

        # Load from _builtins.yaml
        builtins_path = _ASSETS_DIR / "recipes" / "_builtins.yaml"
        if not builtins_path.exists():
            logger.warning("Recipe builtins file not found")
            return None

        try:
            with open(builtins_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            recipes = data.get("recipes", {})
            if recipe_id not in recipes:
                logger.warning(f"Recipe not found: {recipe_id}")
                return None

            recipe_data = recipes[recipe_id]
            config = RecipeConfig(
                anchor=recipe_data["anchor"],
                persona=recipe_data["persona"],
                profession=recipe_data["profession"],
                domain=recipe_data.get("domain"),
                description=recipe_data.get("description", ""),
                version=recipe_data.get("version", "1.0"),
                backward_compatible=recipe_data.get("backward_compatible", False),
                legacy_id=recipe_data.get("legacy_id"),
            )

            self._cache[recipe_id] = config
            return config

        except (yaml.YAMLError, OSError, KeyError) as e:
            logger.error(f"Failed to load Recipe {recipe_id}: {e}")
            return None

    def load_by_legacy_id(self, legacy_id: str) -> RecipeConfig | None:
        """Load a Recipe by its legacy (backward compatible) ID."""
        builtins_path = _ASSETS_DIR / "recipes" / "_builtins.yaml"

        try:
            with open(builtins_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)

            recipes = data.get("recipes", {})
            for recipe_id, recipe_data in recipes.items():
                if recipe_data.get("legacy_id") == legacy_id:
                    return self.load(recipe_id)

            return None

        except (yaml.YAMLError, OSError) as e:
            logger.error(f"Failed to load Recipe by legacy_id {legacy_id}: {e}")
            return None


# Global loader instances (lazy singleton)
_anchor_loader: AnchorLoader | None = None
_persona_loader: PersonaLoader | None = None
_profession_loader: ProfessionLoader | None = None
_recipe_loader: RecipeLoader | None = None


def get_anchor_loader() -> AnchorLoader:
    """Get the global AnchorLoader instance."""
    global _anchor_loader
    if _anchor_loader is None:
        _anchor_loader = AnchorLoader()
    return _anchor_loader


def get_persona_loader() -> PersonaLoader:
    """Get the global PersonaLoader instance."""
    global _persona_loader
    if _persona_loader is None:
        _persona_loader = PersonaLoader()
    return _persona_loader


def get_profession_loader() -> ProfessionLoader:
    """Get the global ProfessionLoader instance."""
    global _profession_loader
    if _profession_loader is None:
        _profession_loader = ProfessionLoader()
    return _profession_loader


def get_recipe_loader() -> RecipeLoader:
    """Get the global RecipeLoader instance."""
    global _recipe_loader
    if _recipe_loader is None:
        _recipe_loader = RecipeLoader()
    return _recipe_loader
