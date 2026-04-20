"""Blueprint analysis and technology stack detection."""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def detect_tech_stack(
    requirements: str,
    plan_text: str = "",
    workspace_files: list[str] | None = None,
) -> dict[str, Any]:
    """Detect technology stack from requirements and project context.

    For an unmanned automated factory, PM must autonomously infer the
    language and framework from project docs, not rely on manual directives.

    Args:
        requirements: Requirements document text
        plan_text: Plan document text (optional)
        workspace_files: List of workspace file paths (optional)

    Returns:
        Dict with keys: language, framework, project_type, confidence,
        alternative_languages
    """
    text = f"{requirements} {plan_text}".lower()

    # Language detection patterns (regex with token boundaries to avoid false positives,
    # e.g., "architecture" should not trigger React/TypeScript).
    lang_patterns = {
        "python": [
            r"\bpython\b",
            r"\bfastapi\b",
            r"\bflask\b",
            r"\bdjango\b",
            r"\bpytest\b",
            r"\.py\b",
            r"requirements\.txt",
        ],
        "typescript": [
            r"\btypescript\b",
            r"\bts-node\b",
            r"tsconfig\.json",
            r"\.tsx?\b",
            r"\bnestjs\b",
        ],
        "javascript": [
            r"\bjavascript\b",
            r"\bnode\.?js\b",
            r"\bexpress\b",
            r"\.jsx?\b",
        ],
        "go": [
            r"\bgolang\b",
            r"go\.mod",
            r"\.go\b",
            r"\bgin\b",
            r"\bfiber\b",
        ],
        "rust": [
            r"\brust\b",
            r"\bcargo\b",
            r"cargo\.toml",
            r"\.rs\b",
            r"\baxum\b",
            r"\bactix\b",
        ],
        "java": [
            r"\bjava\b",
            r"\bspring\b",
            r"\bspringboot\b",
            r"\bmaven\b",
            r"\bgradle\b",
            r"pom\.xml",
        ],
    }

    framework_patterns = {
        "fastapi": [r"\bfastapi\b", r"\bfast api\b"],
        "flask": [r"\bflask\b"],
        "django": [r"\bdjango\b"],
        "react": [r"\breact\b", r"\bnextjs\b", r"\bnext\.js\b"],
        "vue": [r"\bvue\b", r"\bvue\.js\b"],
        "nestjs": [r"\bnestjs\b", r"\bnest\.js\b"],
        "express": [r"\bexpress\b"],
        "gin": [r"\bgin\b", r"\bgin-gonic\b"],
        "echo": [r"\becho\b"],
        "actix": [r"\bactix\b"],
        "axum": [r"\baxum\b"],
    }

    lang_scores: dict[str, int] = {}
    for lang, patterns in lang_patterns.items():
        score = sum(1 for pattern in patterns if re.search(pattern, text))
        if score > 0:
            lang_scores[lang] = score

    detected_lang = max(lang_scores, key=lambda k: lang_scores.get(k, 0)) if lang_scores else "unknown"
    lang_confidence = lang_scores.get(detected_lang, 0) / max(lang_scores.values()) if lang_scores else 0

    detected_framework = None
    for fw, patterns in framework_patterns.items():
        if any(re.search(pattern, text) for pattern in patterns):
            detected_framework = fw
            break

    framework_language_map = {
        "python": {"fastapi", "flask", "django"},
        "typescript": {"react", "vue", "nestjs", "express"},
        "javascript": {"react", "vue", "express"},
        "go": {"gin", "echo"},
        "rust": {"actix", "axum"},
        "java": set(),
    }
    allowed_frameworks = framework_language_map.get(detected_lang, set())
    if detected_framework and allowed_frameworks and detected_framework not in allowed_frameworks:
        detected_framework = None

    project_type = "generic"
    if re.search(r"\bapi\b|\brest\b|\bendpoint\b|\bserver\b", text):
        project_type = "api"
    elif re.search(r"\bcli\b|\bcommand\b|\bterminal\b|\btool\b", text):
        project_type = "cli"
    elif re.search(r"\bweb\b|\bfrontend\b|\bui\b|\binterface\b", text):
        project_type = "web"
    elif re.search(r"\bservice\b|\bmicroservice\b", text):
        project_type = "microservice"
    elif re.search(r"\blibrary\b|\bpackage\b|\bsdk\b", text):
        project_type = "library"

    return {
        "language": detected_lang,
        "framework": detected_framework,
        "project_type": project_type,
        "confidence": lang_confidence,
        "alternative_languages": (
            sorted(lang_scores.keys(), key=lambda k: lang_scores[k], reverse=True)[1:3] if len(lang_scores) > 1 else []
        ),
    }


def build_enhanced_task_with_tech_stack(
    base_task: dict[str, Any],
    tech_stack: dict[str, Any],
    requirements: str,
) -> dict[str, Any]:
    """Enhance task description with detected technology stack.

    Ensures the task title, goal, and metadata clearly specify
    the target language and framework for the Worker.

    Args:
        base_task: Base task dictionary
        tech_stack: Detected technology stack dict
        requirements: Requirements document text

    Returns:
        Enhanced task dictionary with tech stack information
    """
    lang = tech_stack.get("language", "unknown")
    framework = tech_stack.get("framework")
    project_type = tech_stack.get("project_type", "generic")
    stack_label = f"{lang.title()} {framework.title()}".strip() if framework else str(lang).title()

    # Build enhanced title
    original_title = str(base_task.get("title") or "")
    original_title_lower = original_title.lower()
    if "requirements fallback" in original_title_lower and lang != "unknown":
        if "tests" in original_title_lower or "verification" in original_title_lower:
            base_task["title"] = f"Requirements tests ({stack_label} {project_type.title()})"
        elif "implementation" in original_title_lower:
            base_task["title"] = f"Requirements implementation ({stack_label} {project_type.title()})"
        else:
            base_task["title"] = f"Requirements bootstrap ({stack_label} {project_type.title()})"

    # Build enhanced goal
    original_goal = str(base_task.get("goal") or "").strip()
    if lang != "unknown":
        stack_phrase = stack_label if framework else lang.title()
        if original_goal:
            if stack_phrase.lower() not in original_goal.lower():
                base_task["goal"] = f"{original_goal} Use {stack_phrase} conventions."
        else:
            base_task["goal"] = f"Implement {project_type} deliverables in {stack_phrase}."

    # Add description with tech stack details
    description_parts = [
        f"Technology Stack: {lang.title()}",
    ]
    if framework:
        description_parts.append(f"Framework: {framework.title()}")
    description_parts.append(f"Project Type: {project_type.title()}")
    description_parts.append(
        f"Requirements Summary: {requirements[:200]}..."
        if len(requirements) > 200
        else f"Requirements Summary: {requirements}"
    )

    base_task["description"] = "\n".join(description_parts)

    # Store tech stack in metadata for Worker
    if "metadata" not in base_task:
        base_task["metadata"] = {}
    base_task["metadata"]["tech_stack"] = tech_stack
    base_task["metadata"]["detected_language"] = lang
    base_task["metadata"]["detected_framework"] = framework
    base_task["metadata"]["project_type"] = project_type

    return base_task


__all__ = [
    "build_enhanced_task_with_tech_stack",
    "detect_tech_stack",
]
