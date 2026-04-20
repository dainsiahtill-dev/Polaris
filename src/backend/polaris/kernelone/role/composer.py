"""Role Composer - Tri-Axis Role Composition Engine.

Composes Anchor + Profession + Persona into a complete System Prompt.

Formula: Final Execution = System_Anchor ⊕ Profession ⊕ Persona
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from polaris.kernelone.role.loaders import (
    AnchorConfig,
    PersonaConfig,
    ProfessionConfig,
    get_anchor_loader,
    get_persona_loader,
    get_profession_loader,
    get_recipe_loader,
)

logger = logging.getLogger(__name__)


@dataclass
class PromptMetadata:
    """Metadata about the composed prompt."""

    anchor_id: str
    persona_id: str
    profession_id: str
    domain: str | None = None
    task_type: str = "default"
    version: str = "1.0"
    cache_key: str = ""


@dataclass
class ComposedPrompt:
    """A composed prompt from three axes."""

    system_prompt: str
    metadata: PromptMetadata
    workflow: dict[str, Any] = field(default_factory=dict)
    engineering_standards: dict[str, Any] = field(default_factory=dict)
    task_protocols: dict[str, Any] = field(default_factory=dict)
    output_format: dict[str, Any] = field(default_factory=dict)


class RoleComposer:
    """
    Role Composition Engine.

    Composes System Anchor + Profession + Persona into a complete System Prompt.
    This implements the "Tri-Axis Orthogonal Composition" design.

    Usage:
        composer = RoleComposer()
        composed = composer.compose(
            anchor_id="harbor_pilot_director",
            profession_id="python_principal_architect",
            persona_id="gongbu_shilang",
            task_type="new_code"
        )
        print(composed.system_prompt)
    """

    def __init__(self) -> None:
        self._anchor_loader = get_anchor_loader()
        self._persona_loader = get_persona_loader()
        self._profession_loader = get_profession_loader()
        self._recipe_loader = get_recipe_loader()

        # Cache for composed prompts
        self._cache: dict[str, ComposedPrompt] = {}

    def compose(
        self,
        anchor_id: str,
        profession_id: str,
        persona_id: str,
        task_type: str = "default",
        domain: str | None = None,
        skip_cache: bool = False,
    ) -> ComposedPrompt | None:
        """
        Compose a complete System Prompt from three axes.

        Args:
            anchor_id: System Anchor ID (e.g., "harbor_pilot_director")
            profession_id: Profession ID (e.g., "python_principal_architect")
            persona_id: Persona ID (e.g., "gongbu_shilang")
            task_type: Task type for selecting workflow stages (e.g., "new_code", "refactor")
            domain: Optional domain ID (e.g., "python_backend")
            skip_cache: Skip cache and regenerate

        Returns:
            ComposedPrompt with system_prompt and metadata, or None if any config is missing
        """
        # Generate cache key
        cache_key = self._make_cache_key(anchor_id, profession_id, persona_id, domain, task_type)

        if not skip_cache and cache_key in self._cache:
            logger.debug(f"Cache hit for {cache_key}")
            return self._cache[cache_key]

        # Load all three axes
        anchor = self._anchor_loader.load(anchor_id)
        persona = self._persona_loader.load(persona_id)
        profession = self._profession_loader.load(profession_id)

        if not anchor:
            logger.error(f"Failed to load Anchor: {anchor_id}")
            return None
        if not persona:
            logger.error(f"Failed to load Persona: {persona_id}")
            return None
        if not profession:
            logger.error(f"Failed to load Profession: {profession_id}")
            return None

        # Build the prompt layers
        identity_prompt = self._build_identity_prompt(anchor, profession, persona)
        workflow_prompt = self._build_workflow_prompt(anchor, profession, task_type)
        standards_prompt = self._build_standards_prompt(profession)
        protocols_prompt = self._build_protocols_prompt(profession, task_type)
        format_prompt = self._build_format_prompt(profession, task_type)

        # Assemble the final prompt
        system_prompt = self._assemble(
            identity_prompt,
            workflow_prompt,
            standards_prompt,
            protocols_prompt,
            format_prompt,
        )

        # Build workflow and standards for runtime use
        composed = ComposedPrompt(
            system_prompt=system_prompt,
            metadata=PromptMetadata(
                anchor_id=anchor_id,
                persona_id=persona_id,
                profession_id=profession_id,
                domain=domain,
                task_type=task_type,
                version=profession.version,
                cache_key=cache_key,
            ),
            workflow=profession.workflow,
            engineering_standards=profession.engineering_standards,
            task_protocols=profession.task_protocols,
            output_format=profession.output_format,
        )

        # Cache the result
        self._cache[cache_key] = composed
        logger.debug(f"Cached composed prompt: {cache_key}")

        return composed

    def compose_by_recipe(self, recipe_id: str, task_type: str = "default") -> ComposedPrompt | None:
        """
        Compose a prompt using a Recipe (predefined combination).

        Args:
            recipe_id: Recipe ID (e.g., "senior_python_architect")
            task_type: Task type for selecting workflow stages

        Returns:
            ComposedPrompt, or None if recipe not found
        """
        recipe = self._recipe_loader.load(recipe_id)
        if not recipe:
            # Try legacy ID
            legacy_recipe = self._recipe_loader.load_by_legacy_id(recipe_id)
            if legacy_recipe:
                recipe = legacy_recipe

        if not recipe:
            logger.error(f"Recipe not found: {recipe_id}")
            return None

        return self.compose(
            anchor_id=recipe.anchor,
            profession_id=recipe.profession,
            persona_id=recipe.persona,
            task_type=task_type,
            domain=recipe.domain,
        )

    def _make_cache_key(
        self,
        anchor_id: str,
        profession_id: str,
        persona_id: str,
        domain: str | None,
        task_type: str,
    ) -> str:
        """Generate a cache key for the composition."""
        key_parts = [anchor_id, profession_id, persona_id, task_type]
        if domain:
            key_parts.append(domain)
        key_string = ":".join(key_parts)
        return hashlib.md5(key_string.encode()).hexdigest()[:16]

    def _build_identity_prompt(
        self,
        anchor: AnchorConfig,
        profession: ProfessionConfig,
        persona: PersonaConfig,
    ) -> str:
        """Build the identity definition prompt."""
        return f"""<role_definition>
# 系统定位
你是 Polaris 体系中的 **{anchor.name}**。
{anchor.description}

# 专业能力
你同时是一位**{profession.name}**。
{profession.identity}

# 表达方式
请保持 **{persona.name}** 的性格特点:
- 性格基调: {persona.traits}
- 语气特点: {persona.tone}
- 特色词汇: {", ".join(persona.vocabulary[:5])}
</role_definition>
"""

    def _build_workflow_prompt(
        self,
        anchor: AnchorConfig,
        profession: ProfessionConfig,
        task_type: str,
    ) -> str:
        """Build the workflow prompt based on task type."""
        workflow = profession.workflow
        stages_raw = workflow.get("stages", {})

        # Handle stages as dict (id -> config) or list (config objects)
        if isinstance(stages_raw, dict):
            # Stages are keyed by id
            stages_dict = stages_raw
            stages_list = list(stages_dict.values())
        elif isinstance(stages_raw, list):
            # Stages are a list of dicts
            stages_dict = {s["id"]: s for s in stages_raw if isinstance(s, dict) and "id" in s}
            stages_list = [s for s in stages_raw if isinstance(s, dict) and "id" in s]
        else:
            return ""

        # Get stages for this task type
        task_mapping = workflow.get("task_type_mapping", {})
        applicable_stages = task_mapping.get(task_type, list(stages_dict.keys()))

        # Filter stages based on task type
        if task_type == "default" or not applicable_stages:
            filtered_stages = stages_list
        else:
            filtered_stages = [s for s in stages_list if s.get("id") in applicable_stages]

        if not filtered_stages:
            return ""

        # Build workflow prompt
        workflow_lines = ["<workflow>"]
        workflow_lines.append(f"工作流程类型: {workflow.get('type', 'sequential')}")
        workflow_lines.append("\n执行阶段:")

        for i, stage in enumerate(filtered_stages, 1):
            if not isinstance(stage, dict):
                continue
            stage_id = stage.get("id", "unknown")
            stage_name = stage.get("name", "Unknown")
            stage_desc = stage.get("description", "")

            workflow_lines.append(f"\n  阶段 {i}: {stage_name} ({stage_id})")
            workflow_lines.append(f"    {stage_desc}")

            # Add trigger conditions if present
            if "trigger_on" in stage:
                triggers = stage["trigger_on"]
                workflow_lines.append(f"    触发条件: {', '.join(triggers)}")

        workflow_lines.append("</workflow>")

        return "\n".join(workflow_lines)

    def _build_standards_prompt(self, profession: ProfessionConfig) -> str:
        """Build the engineering standards prompt."""
        standards = profession.engineering_standards
        if not standards:
            return ""

        coverage_mode = standards.get("coverage_mode", "inherit")
        standards_list = standards.get("standards", {})

        lines = ["<engineering_standards>"]
        lines.append(f"标准覆盖模式: {coverage_mode}")

        if standards_list:
            lines.append("\n工程标准:")
            for category, items in standards_list.items():
                lines.append(f"\n  [{category.upper()}]")
                for item in items:
                    lines.append(f"    - {item}")

        # Add red lines
        red_lines = standards.get("red_lines", [])
        if red_lines:
            lines.append("\n【红线 - 绝对禁止】")
            for line in red_lines:
                lines.append(f"  ⚠️ {line}")

        lines.append("</engineering_standards>")

        return "\n".join(lines)

    def _build_protocols_prompt(
        self,
        profession: ProfessionConfig,
        task_type: str,
    ) -> str:
        """Build the task protocols prompt."""
        protocols = profession.task_protocols
        if not protocols:
            return ""

        # Get protocol for this task type
        protocol = protocols.get(task_type, protocols.get("default", {}))

        lines = ["<task_protocols>"]
        lines.append(f"任务类型: {task_type}")

        for key, value in protocol.items():
            if isinstance(value, bool):
                lines.append(f"  - {key}: {'是' if value else '否'}")
            elif isinstance(value, (int, str)):
                lines.append(f"  - {key}: {value}")
            elif isinstance(value, list):
                lines.append(f"  - {key}: {', '.join(str(v) for v in value)}")
            elif isinstance(value, dict):
                lines.append(f"  - {key}:")
                for k, v in value.items():
                    lines.append(f"      {k}: {v}")

        lines.append("</task_protocols>")

        return "\n".join(lines)

    def _build_format_prompt(
        self,
        profession: ProfessionConfig,
        task_type: str,
    ) -> str:
        """Build the output format prompt."""
        output_format = profession.output_format
        if not output_format:
            return ""

        default_format = output_format.get("default", "standard")
        formats = output_format.get("formats", {})

        lines = ["<output_format>"]
        lines.append(f"默认格式: {default_format}")

        # Add section structure
        if default_format in formats:
            format_config = formats[default_format]
            sections = format_config.get("sections", [])

            lines.append("\n输出结构:")
            for section in sections:
                if isinstance(section, dict):
                    section_id = section.get("id", "")
                    section_name = section.get("name", section_id)
                    section_desc = section.get("description", "")
                    emoji = section.get("emoji", "")
                    lines.append(f"  {emoji} {section_name}")
                    if section_desc:
                        lines.append(f"      {section_desc}")
                else:
                    lines.append(f"  - {section}")

        lines.append("</output_format>")

        return "\n".join(lines)

    def _assemble(
        self,
        identity_prompt: str,
        workflow_prompt: str,
        standards_prompt: str,
        protocols_prompt: str,
        format_prompt: str,
    ) -> str:
        """Assemble all prompt parts into the final system prompt."""
        parts = [
            identity_prompt,
        ]

        if workflow_prompt:
            parts.append(workflow_prompt)

        if standards_prompt:
            parts.append(standards_prompt)

        if protocols_prompt:
            parts.append(protocols_prompt)

        if format_prompt:
            parts.append(format_prompt)

        return "\n\n".join(parts)

    def invalidate_cache(self, anchor_id: str | None = None) -> None:
        """Invalidate cache entries. If anchor_id is None, clear all."""
        if anchor_id is None:
            self._cache.clear()
            logger.info("Cleared all composed prompt cache")
        else:
            keys_to_remove = [k for k, v in self._cache.items() if v.metadata.anchor_id == anchor_id]
            for key in keys_to_remove:
                del self._cache[key]
            logger.info(f"Invalidated {len(keys_to_remove)} cache entries for anchor {anchor_id}")


# Global composer instance
_composer: RoleComposer | None = None


def get_role_composer() -> RoleComposer:
    """Get the global RoleComposer instance."""
    global _composer
    if _composer is None:
        _composer = RoleComposer()
    return _composer
