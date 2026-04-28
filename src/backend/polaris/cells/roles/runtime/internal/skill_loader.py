"""
Skill Module - 两层技能加载系统.

DEPRECATED: This module is being unified with KernelOne skill system.
Use polaris.kernelone.single_agent.skill_system instead.

Layer 1 (System Prompt): 技能元数据 (name, description, tags)
Layer 2 (On Demand): 完整技能内容 (按需加载)
"""

from __future__ import annotations

import logging
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """技能数据模型 (兼容层)"""

    name: str
    description: str
    body: str
    tags: list[str] = field(default_factory=list)
    path: str = ""
    metadata: dict = field(default_factory=dict)


class SkillLoader:
    """
    两层技能加载器 (兼容层，委托给 KernelOne 实现)

    DEPRECATED: Use polaris.kernelone.single_agent.skill_system.SkillLoader instead.
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        warnings.warn(
            "cells.runtime.internal.skill_loader.SkillLoader is deprecated. "
            "Use kernelone.single_agent.skill_system.SkillLoader instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.skills_dir = skills_dir
        self.skills: dict[str, Skill] = {}
        self._kernel_loader: Any = None

        if skills_dir and skills_dir.exists():
            # When skills_dir is provided, use legacy loading for backward compat
            self._load_all_legacy()
        else:
            # Try to delegate to KernelOne implementation when no explicit dir
            try:
                from polaris.kernelone.single_agent.skill_system import (
                    SkillLoader as KernelSkillLoader,
                )

                workspace = str(skills_dir.parent) if skills_dir else "."
                self._kernel_loader = KernelSkillLoader(workspace)
                self._sync_from_kernel()
            except (ImportError, RuntimeError, OSError):
                pass

    def _sync_from_kernel(self) -> None:
        """Sync skills from KernelOne loader to compatible format."""
        if not self._kernel_loader:
            return
        for name, skill in self._kernel_loader._skills.items():
            self.skills[name] = Skill(
                name=skill.name,
                description=skill.description,
                body=skill.body,
                tags=skill.tags,
                path=skill.path,
                metadata=skill.meta,
            )

    @staticmethod
    def resolve_default_skills_dir(workspace: str) -> Path:
        return Path(workspace).resolve() / ".skills"

    @staticmethod
    def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
        """Parse markdown frontmatter."""
        match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.DOTALL)
        if not match:
            return {}, text.strip()

        meta_text = str(match.group(1) or "").strip()
        body = str(match.group(2) or "").strip()

        metadata: dict[str, Any] = {}
        for line in meta_text.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            metadata[str(key).strip()] = str(value).strip()
        return metadata, body

    @staticmethod
    def _parse_tags(raw_tags: Any) -> list[str]:
        token = str(raw_tags or "").strip()
        if not token:
            return []
        if token.startswith("[") and token.endswith("]"):
            token = token[1:-1].strip()
        tags = [part.strip(" '\"\t") for part in token.split(",")]
        return [tag for tag in tags if tag]

    def _load_all_legacy(self) -> None:
        """Legacy skill loading (fallback)."""
        if not self.skills_dir:
            return

        for file_path in sorted(self.skills_dir.glob("*.md")):
            try:
                self._load_skill_file(file_path)
            except (RuntimeError, ValueError) as e:
                logger.debug(f"Failed to load skill file {file_path}: {e}")

    def _load_skill_file(self, file_path: Path) -> None:
        """加载单个技能文件"""
        text = file_path.read_text(encoding="utf-8")

        meta, body = self._parse_frontmatter(text)
        tags = self._parse_tags(meta.get("tags"))

        file_name = file_path.stem.strip()
        declared_name = str(meta.get("name") or "").strip()
        skill_name = declared_name or file_name

        skill = Skill(
            name=skill_name,
            description=meta.get("description", ""),
            body=body,
            tags=tags,
            path=str(file_path),
            metadata=meta,
        )

        self.skills[skill.name] = skill

    def _load_all(self) -> None:
        """加载所有技能"""
        if self._kernel_loader:
            self._sync_from_kernel()
        elif self.skills_dir and self.skills_dir.exists():
            self._load_all_legacy()

    def get_manifest(self) -> list[dict]:
        """Layer 1: 获取技能清单 (元数据)"""
        if self._kernel_loader:
            return self._kernel_loader.list_skills()

        manifest = [
            {
                "name": s.name,
                "description": s.description,
                "tags": s.tags,
                "path": s.path,
            }
            for s in sorted(self.skills.values(), key=lambda item: item.name.lower())
        ]
        return manifest

    def get_manifest_text(self) -> str:
        """Layer 1: 获取技能清单文本 (用于 System Prompt)"""
        if self._kernel_loader:
            text = self._kernel_loader.get_system_prompt_section()
            # Normalize case for backward compatibility
            return text.replace("(No skills available)", "(no skills available)")

        if not self.skills:
            return "(no skills available)"

        lines = []
        for skill in sorted(self.skills.values(), key=lambda item: item.name.lower()):
            line = f"  - {skill.name}: {skill.description}"
            if skill.tags:
                line += f" [{', '.join(skill.tags)}]"
            lines.append(line)

        return "\n".join(lines)

    def get_content(self, name: str) -> str:
        """Layer 2: 获取完整技能内容"""
        if self._kernel_loader:
            return self._kernel_loader.load_skill_content(name)

        skill = self.skills.get(name)
        if not skill:
            return f"Error: Unknown skill '{name}'. Available: {', '.join(self.skills.keys())}"

        return f'<skill name="{skill.name}">\n{skill.body}\n</skill>'

    def has_skill(self, name: str) -> bool:
        """检查技能是否存在"""
        if self._kernel_loader:
            return self._kernel_loader.get_skill(name) is not None
        return name in self.skills

    def list_skills(self, tag: str | None = None) -> list[str]:
        """列出技能名称"""
        if self._kernel_loader:
            skills = self._kernel_loader.list_skills()
            if tag:
                return [s["name"] for s in skills if tag in s.get("tags", [])]
            return [s["name"] for s in skills]

        if tag:
            return [s.name for s in self.skills.values() if tag in s.tags]
        return list(self.skills.keys())


class RoleSkillManager:
    """
    角色技能管理器

    DEPRECATED: Use kernelone skill system instead.
    """

    DEFAULT_SKILLS = {
        "PM": ["task-planning", "priority-management", "coordination"],
        "Director": ["execution", "verification", "debug"],
        "Architect": ["architecture", "code-review", "design-patterns"],
        "ChiefEngineer": ["implementation", "refactor", "testing"],
        "CFO": ["budget", "cost-analysis"],
        "HR": ["llm-config", "provider-management"],
        "QA": ["security-audit", "quality-check"],
        "Auditor": ["review", "compliance"],
    }

    def __init__(self, skills_dir: Path | None = None) -> None:
        warnings.warn(
            "RoleSkillManager is deprecated. Use kernelone skill system instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.loader = SkillLoader(skills_dir)
        self._role_skills: dict[str, list[str]] = {}

    def register_role_skills(self, role: str, skills: list[str]) -> None:
        """为角色注册技能"""
        self._role_skills[role] = skills

    def get_role_manifest(self, role: str) -> str:
        """获取角色的技能清单 (Layer 1)"""
        skills = self._role_skills.get(role, self.DEFAULT_SKILLS.get(role, []))

        lines = []
        for skill_name in skills:
            skill = self.loader.skills.get(skill_name)
            if skill:
                line = f"  - {skill.name}: {skill.description}"
                if skill.tags:
                    line += f" [{', '.join(skill.tags)}]"
                lines.append(line)

        if not lines:
            return "(no skills)"

        return "\n".join(lines)

    def load_role_skill(self, role: str, skill_name: str) -> str:
        """加载角色的技能内容 (Layer 2)"""
        return self.loader.get_content(skill_name)


def create_skill_loader(skills_dir: Path) -> SkillLoader:
    """创建技能加载器 (兼容层)"""
    return SkillLoader(skills_dir)


def create_role_skill_manager(skills_dir: Path) -> RoleSkillManager:
    """创建角色技能管理器 (兼容层)"""
    return RoleSkillManager(skills_dir)
