"""Pure task classification + tech-stack inference for Director workers.

Extracted verbatim from ``worker_executor.WorkerExecutor`` (G7 decomposition,
step 2). These are pure free functions that take a ``task`` and read only its
``subject``/``description``/``metadata`` fields; they hold no ``self`` state.

Depends only on the standard library so it can never participate in the lazy
circular-import dance documented in ``worker_executor`` (MUST NOT import
``code_generation_engine`` / ``file_apply_service`` at module top).

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

import re
from typing import cast

from polaris.domain.entities import Task


def classify_task(task: Task) -> str:
    """Classify task type based on subject and description."""
    subject = task.subject.lower()
    description = task.description.lower()

    # Bootstrap tasks
    if "bootstrap" in subject or "init" in subject:
        return "bootstrap"

    # File creation tasks
    if "create file" in subject or "create directory" in subject:
        return "file_creation"

    # Code generation tasks
    if any(
        kw in subject or kw in description
        for kw in [
            "implement",
            "create",
            "build",
            "generate",
            "function",
            "class",
            "module",
            "api",
            "endpoint",
        ]
    ):
        return "code_generation"

    return "generic"


def extract_tech_stack(task: Task) -> dict[str, str]:
    """Extract technology stack from task metadata (set by PM)."""
    tech_stack: dict[str, str] = {}

    # First try to get from metadata (new PM sets this)
    if task.metadata:
        if "tech_stack" in task.metadata:
            return cast("dict[str, str]", dict(task.metadata["tech_stack"]))
        if "detected_language" in task.metadata:
            tech_stack["language"] = task.metadata["detected_language"]
        if "detected_framework" in task.metadata:
            tech_stack["framework"] = task.metadata["detected_framework"]
        if "project_type" in task.metadata:
            tech_stack["project_type"] = task.metadata["project_type"]
        if tech_stack:
            return tech_stack

    # Fallback: detect from task description
    description = (task.description or "").lower()
    subject = (task.subject or "").lower()
    text = f"{subject} {description}"

    language_patterns: dict[str, list[str]] = {
        "python": [
            r"\bpython\b",
            r"\bfastapi\b",
            r"\bflask\b",
            r"\bdjango\b",
            r"\bpytest\b",
            r"requirements\.txt",
            r"\.py\b",
        ],
        "typescript": [
            r"\btypescript\b",
            r"\bts-node\b",
            r"tsconfig\.json",
            r"\bts\b(?=\s+(project|service|app|api|module|code|conventions))",
            r"\.tsx?\b",
        ],
        "javascript": [
            r"\bjavascript\b",
            r"\bnode\.?js\b",
            r"\bnode\b",
            r"\bexpress\b",
            r"\bjs\b(?=\s+(project|service|app|api|module|code|conventions))",
            r"\.jsx?\b",
        ],
        "go": [
            r"\bgolang\b",
            r"\bgo\b(?=\s+(project|service|app|api|module|code|conventions|test|build))",
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
        ],
        "java": [
            r"\bjava\b",
            r"\bspring\b",
            r"\bgradle\b",
            r"pom\.xml",
        ],
    }
    language_scores: dict[str, int] = {}
    for language, patterns in language_patterns.items():
        score = sum(1 for pattern in patterns if re.search(pattern, text))
        if score > 0:
            language_scores[language] = score
    if language_scores:
        tech_stack["language"] = max(language_scores, key=language_scores.get)  # type: ignore[arg-type]
    else:
        tech_stack["language"] = "unknown"

    framework_patterns: dict[str, str] = {
        "fastapi": r"\bfastapi\b",
        "flask": r"\bflask\b",
        "django": r"\bdjango\b",
        "react": r"\breact\b",
        "vue": r"\bvue\b",
        "express": r"\bexpress\b",
    }
    for framework, pattern in framework_patterns.items():
        if re.search(pattern, text):
            tech_stack["framework"] = framework
            break

    if re.search(r"\bapi\b|\brest\b|\bendpoint\b", text):
        tech_stack["project_type"] = "api"
    elif re.search(r"\bcli\b|\bcommand\b|\bterminal\b", text):
        tech_stack["project_type"] = "cli"
    elif re.search(r"\bweb\b|\bfrontend\b|\bui\b", text):
        tech_stack["project_type"] = "web"
    elif re.search(r"\bservice\b|\bmicroservice\b", text):
        tech_stack["project_type"] = "microservice"
    elif re.search(r"\blibrary\b|\bpackage\b|\bsdk\b", text):
        tech_stack["project_type"] = "library"
    else:
        tech_stack["project_type"] = "generic"

    return tech_stack
