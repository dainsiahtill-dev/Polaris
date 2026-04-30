"""Skill template service for Polaris backend.

Provides loading and management of skill templates.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SkillTemplate:
    """A skill template."""

    name: str
    description: str
    tags: list[str]
    content: str
    source_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "content": self.content[:200] + "..." if len(self.content) > 200 else self.content,
        }


class SkillTemplateService:
    """Service for managing skill templates.

    Provides:
    - Loading skills from filesystem
    - Metadata extraction (YAML frontmatter)
    - Tag-based filtering
    - Content retrieval
    """

    def __init__(self, skills_dir: Path | str) -> None:
        """Initialize skill template service.

        Args:
            skills_dir: Directory containing skill markdown files
        """
        self.skills_dir = Path(skills_dir)
        self._skills: dict[str, SkillTemplate] = {}
        self._load_all_skills()

    def _load_all_skills(self) -> None:
        """Load all skills from directory."""
        if not self.skills_dir.exists():
            return

        for skill_file in self.skills_dir.glob("*.md"):
            try:
                skill = self._parse_skill_file(skill_file)
                self._skills[skill.name] = skill
            except (RuntimeError, ValueError) as e:
                logger.warning("Failed to parse skill file %s: %s", skill_file, e)
                continue

    def _parse_skill_file(self, path: Path) -> SkillTemplate:
        """Parse a skill markdown file.

        Args:
            path: Path to skill file

        Returns:
            Parsed SkillTemplate
        """
        content = path.read_text(encoding="utf-8")

        # Parse YAML frontmatter
        frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)

        if not frontmatter_match:
            raise ValueError(f"No YAML frontmatter in {path}")

        frontmatter_text = frontmatter_match.group(1)
        skill_content = content[frontmatter_match.end() :]

        # Parse frontmatter
        metadata = self._parse_frontmatter(frontmatter_text)

        return SkillTemplate(
            name=metadata.get("name", path.stem),
            description=metadata.get("description", ""),
            tags=metadata.get("tags", []),
            content=skill_content,
            source_path=path,
        )

    def _parse_frontmatter(self, text: str) -> dict[str, Any]:
        """Parse YAML frontmatter text.

        Simple parser for basic YAML structures.
        """
        result: dict[str, Any] = {}
        current_key = None
        current_list: list[str] = []

        for line in text.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Check for list item
            if line.startswith("- "):
                if current_key:
                    current_list.append(line[2:].strip())
                continue
            # Save previous list
            elif current_key and current_list:
                result[current_key] = current_list
                current_list = []

            # Check for key: value
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip()

                # Remove quotes if present
                if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]

                # Remove brackets for list syntax [a, b]
                if value.startswith("[") and value.endswith("]"):
                    value_list: list[str] = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
                    current_key = key
                    result[key] = value_list
                else:
                    result[key] = value
                    current_key = key

        # Save last list
        if current_key and current_list:
            result[current_key] = current_list

        return result

    def get_skill(self, name: str) -> SkillTemplate | None:
        """Get a skill by name.

        Args:
            name: Skill name

        Returns:
            SkillTemplate or None
        """
        return self._skills.get(name)

    def list_skills(self, tag: str | None = None) -> list[dict[str, Any]]:
        """List available skills.

        Args:
            tag: Filter by tag (optional)

        Returns:
            List of skill metadata
        """
        skills_iter = self._skills.values()

        if tag:
            skills_list = [s for s in skills_iter if tag in s.tags]
            return [s.to_dict() for s in skills_list]

        return [s.to_dict() for s in skills_iter]

    def get_skill_content(self, name: str) -> str | None:
        """Get full skill content.

        Args:
            name: Skill name

        Returns:
            Full skill content or None
        """
        skill = self._skills.get(name)
        return skill.content if skill else None

    def has_skill(self, name: str) -> bool:
        """Check if a skill exists.

        Args:
            name: Skill name

        Returns:
            True if skill exists
        """
        return name in self._skills


# Global instance
_skill_service: SkillTemplateService | None = None


def get_skill_template_service(skills_dir: Path | str | None = None) -> SkillTemplateService:
    """Get global skill template service.

    Args:
        skills_dir: Directory containing skills (uses project root/skills if None)

    Returns:
        SkillTemplateService instance
    """
    global _skill_service

    if _skill_service is None:
        if skills_dir is None:
            # Find skills directory relative to this file
            skills_dir = Path(__file__).parent.parent.parent.parent.parent / "skills"
        _skill_service = SkillTemplateService(skills_dir)

    return _skill_service


def reset_skill_template_service() -> None:
    """Reset global skill service (for testing)."""
    global _skill_service
    _skill_service = None
