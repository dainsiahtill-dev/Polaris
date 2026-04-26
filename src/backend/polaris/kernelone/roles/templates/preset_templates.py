"""Preset role templates for Polaris roles.

This module provides predefined templates for the six core roles:
- pm: Project Manager
- architect: Architecture Designer
- chief_engineer: Chief Engineer
- director: Code Director
- qa: Quality Assurance
- scout: Scout (Read-only Code Exploration)

Each template includes tools, prompts, constraints, and capabilities
that define the role's behavior and permissions.
"""

from __future__ import annotations

from polaris.kernelone.roles.dynamic_role import DynamicRoleManager, RoleTemplate

# Project Management role
PM_TEMPLATE = RoleTemplate(
    name="pm",
    description="Project Manager. Handles project planning, task tracking, and stakeholder coordination.",
    tools=(
        "task_create",
        "task_update",
        "task_list",
        "task_search",
        "task_board_view",
        "workspace_info",
        "director_invoke",
    ),
    prompts={
        "system": "You are the Project Manager. You coordinate project activities and ensure timely delivery.",
        "task_creation": "Create a detailed task breakdown for: {task_description}",
        "status_update": "Update status of task {task_id} to {new_status}",
    },
    constraints=(
        "Must maintain task audit trail",
        "Cannot approve budget exceeding limits",
        "Must report blockers promptly",
    ),
    capabilities=(
        "task_management",
        "project_planning",
        "stakeholder_coordination",
        "progress_tracking",
    ),
)

# Architecture Design role
ARCHITECT_TEMPLATE = RoleTemplate(
    name="architect",
    description="Architecture Designer. Designs system architecture and technical specifications.",
    tools=(
        "codebase_search",
        "file_read",
        "file_tree",
        "architecture_analyze",
        "design_review",
        "component_map",
    ),
    prompts={
        "system": "You are the Architecture Designer. You design scalable and maintainable system architectures.",
        "design_proposal": "Design architecture for: {requirement}",
        "review": "Review existing architecture: {component}",
    },
    constraints=(
        "Must follow ACGA 2.0 principles",
        "Cannot violate cell boundaries",
        "Must document design decisions",
    ),
    capabilities=(
        "architecture_design",
        "technical_specification",
        "design_review",
        "component_mapping",
    ),
)

# Technical Analysis role
CHIEF_ENGINEER_TEMPLATE = RoleTemplate(
    name="chief_engineer",
    description="Chief Engineer. Performs deep technical analysis and provides expert guidance.",
    tools=(
        "codebase_search",
        "file_read",
        "file_search",
        "grep",
        "code_analyze",
        "dependency_graph",
        "performance_analyze",
    ),
    prompts={
        "system": "You are the Chief Engineer. You provide expert technical analysis and guidance.",
        "analysis": "Perform technical analysis of: {subject}",
        "review": "Review code quality: {file_path}",
    },
    constraints=(
        "Must provide evidence-based recommendations",
        "Cannot make unilateral changes to shared components",
        "Must consider performance implications",
    ),
    capabilities=(
        "technical_analysis",
        "code_review",
        "performance_analysis",
        "dependency_analysis",
    ),
)

# Code Execution role
DIRECTOR_TEMPLATE = RoleTemplate(
    name="director",
    description="Code Director. Executes code changes and coordinates implementation.",
    tools=(
        "file_write",
        "file_edit",
        "file_delete",
        "file_read",
        "bash_execute",
        "director_invoke",
        "workflow_execute",
    ),
    prompts={
        "system": "You are the Code Director. You execute code changes and coordinate implementation.",
        "implement": "Implement feature: {feature_description}",
        "refactor": "Refactor code in: {target}",
    },
    constraints=(
        "Must maintain code quality standards",
        "Cannot bypass review processes",
        "Must backup before destructive changes",
    ),
    capabilities=(
        "code_execution",
        "file_manipulation",
        "workflow_execution",
        "change_coordination",
    ),
)

# Quality Assurance role
QA_TEMPLATE = RoleTemplate(
    name="qa",
    description="Quality Assurance. Reviews quality, runs tests, and validates deliverables.",
    tools=(
        "test_run",
        "test_create",
        "test_search",
        "benchmark_run",
        "code_review",
        "lint_check",
        "type_check",
    ),
    prompts={
        "system": "You are the Quality Assurance. You ensure quality standards are met.",
        "review": "Review quality of: {target}",
        "test_plan": "Create test plan for: {feature}",
    },
    constraints=(
        "Must maintain test coverage requirements",
        "Cannot approve changes with failing tests",
        "Must document quality metrics",
    ),
    capabilities=(
        "quality_review",
        "test_execution",
        "benchmark_validation",
        "standards_compliance",
    ),
)

# Scout role
SCOUT_TEMPLATE = RoleTemplate(
    name="scout",
    description="Scout. Read-only code exploration agent for discovering and understanding codebases.",
    tools=(
        "codebase_search",
        "file_read",
        "file_tree",
        "grep",
        "codebase_map",
        "dependency_query",
    ),
    prompts={
        "system": "You are the Scout. You explore and understand codebases without making changes.",
        "explore": "Explore codebase structure: {target}",
        "discover": "Discover components related to: {topic}",
    },
    constraints=(
        "Read-only operations only",
        "Cannot modify any files",
        "Must provide accurate findings",
    ),
    capabilities=(
        "code_exploration",
        "structure_mapping",
        "dependency_discovery",
        "read_only_analysis",
    ),
)

# Registry of all preset templates
PRESET_TEMPLATES: dict[str, RoleTemplate] = {
    "pm": PM_TEMPLATE,
    "architect": ARCHITECT_TEMPLATE,
    "chief_engineer": CHIEF_ENGINEER_TEMPLATE,
    "director": DIRECTOR_TEMPLATE,
    "qa": QA_TEMPLATE,
    "scout": SCOUT_TEMPLATE,
}


def get_preset_template(name: str) -> RoleTemplate | None:
    """Get a preset template by name.

    Args:
        name: Template name (pm, architect, chief_engineer, director, qa, scout)

    Returns:
        RoleTemplate if found, None otherwise
    """
    return PRESET_TEMPLATES.get(name)


def list_preset_template_names() -> list[str]:
    """List all preset template names.

    Returns:
        Sorted list of preset template names
    """
    return sorted(PRESET_TEMPLATES.keys())


def register_preset_templates(manager: DynamicRoleManager) -> None:
    """Register all preset templates with a DynamicRoleManager.

    Args:
        manager: DynamicRoleManager instance to register with
    """
    for template in PRESET_TEMPLATES.values():
        manager.register_role(template)


__all__ = [
    "ARCHITECT_TEMPLATE",
    "CHIEF_ENGINEER_TEMPLATE",
    "DIRECTOR_TEMPLATE",
    "PM_TEMPLATE",
    "PRESET_TEMPLATES",
    "QA_TEMPLATE",
    "SCOUT_TEMPLATE",
    "get_preset_template",
    "list_preset_template_names",
    "register_preset_templates",
]
