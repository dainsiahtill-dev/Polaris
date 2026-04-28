"""Two-layer skill loading system.

Phase 4 implementation from learn-claude-code integration.
Layer 1: Skill metadata in system prompt (~100 tokens/skill)
Layer 2: Full skill body via load_skill tool result

设计约束：
- KernelOne 通用技能加载系统，不嵌入特定产品目录名
- skills_root 由调用方注入，默认从环境变量或 ".polaris/skills" 读取
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polaris.kernelone.storage import resolve_runtime_path

logger = logging.getLogger(__name__)

#: Default skills root. KERNELONE_SKILLS_ROOT env var takes precedence.
_DEFAULT_SKILLS_ROOT = os.environ.get(
    "KERNELONE_SKILLS_ROOT",
    ".polaris/skills",
)


@dataclass
class Skill:
    """A loaded skill with metadata and body."""

    name: str
    description: str
    tags: list[str]
    body: str
    meta: dict[str, Any] = field(default_factory=dict)
    path: str = ""

    @property
    def short_description(self) -> str:
        """One-line description for Layer 1 (system prompt)."""
        tags_str = f" [{', '.join(self.tags)}]" if self.tags else ""
        return f"- {self.name}: {self.description}{tags_str}"

    @property
    def full_content(self) -> str:
        """Full content for Layer 2 (tool result)."""
        return f"""<skill name="{self.name}">
{self.body}
</skill>"""


class SkillLoader:
    """Two-layer skill loading system.

    Avoids bloating system prompt by loading skill content on demand.
    """

    MAX_SKILL_SIZE = 50000  # Max chars per skill

    def __init__(self, workspace: str, *, skills_root: str | None = None) -> None:
        self.workspace = Path(workspace)
        skills_root_str = skills_root or _DEFAULT_SKILLS_ROOT
        self.skills_dir = self.workspace / skills_root_str
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        runtime_skills_dir = Path(resolve_runtime_path(str(self.workspace), "runtime/skills"))
        if runtime_skills_dir.exists():
            for skill_file in runtime_skills_dir.glob("*.md"):
                target = self.skills_dir / skill_file.name
                if target.exists():
                    continue
                try:
                    shutil.copyfile(skill_file, target)
                except (RuntimeError, ValueError) as exc:
                    logger.warning("kernelone.agent.skill_system.copy_skill_file failed: %s", exc, exc_info=True)
                    continue

        self._skills: dict[str, Skill] = {}
        self._load_all_skills()

    def _parse_frontmatter(self, text: str) -> tuple:
        """Parse YAML frontmatter between --- delimiters.

        Returns: (metadata_dict, body_text)
        """
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text

        meta_text = match.group(1).strip()
        body = match.group(2).strip()

        meta = {}
        for line in meta_text.splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip()
                val = val.strip()
                # Handle list syntax
                if val.startswith("[") and val.endswith("]"):
                    val = [v.strip().strip("\"'") for v in val[1:-1].split(",") if v.strip()]
                meta[key] = val

        return meta, body

    def _load_all_skills(self) -> None:
        """Load all skills from skills_dir/*.md"""
        if not self.skills_dir.exists():
            return

        for skill_file in sorted(self.skills_dir.glob("*.md")):
            try:
                text = skill_file.read_text(encoding="utf-8")
                if len(text) > self.MAX_SKILL_SIZE:
                    text = text[: self.MAX_SKILL_SIZE] + "\n... [truncated]"

                meta, body = self._parse_frontmatter(text)

                skill = Skill(
                    name=skill_file.stem,
                    description=meta.get("description", "No description"),
                    tags=meta.get("tags", []) if isinstance(meta.get("tags"), list) else [],
                    body=body,
                    meta=meta,
                    path=str(skill_file),
                )
                self._skills[skill.name] = skill

            except (RuntimeError, ValueError) as e:
                logger.warning("[SkillLoader] Failed to load %s: %s", skill_file, e)

    def get_system_prompt_section(self) -> str:
        """Layer 1: Get skill descriptions for system prompt.

        Returns brief descriptions of all available skills.
        Agent uses this to know which skills to load.
        """
        if not self._skills:
            return "(No skills available)"

        lines = ["Skills available (use load_skill to access full content):"]
        for skill in self._skills.values():
            lines.append(f"  {skill.short_description}")

        return "\n".join(lines)

    def get_skill(self, name: str) -> Skill | None:
        """Get a skill by name."""
        return self._skills.get(name)

    def load_skill_content(self, name: str) -> str:
        """Layer 2: Load full skill content on demand.

        This is called when the model uses the load_skill tool.
        Returns the full skill body wrapped in XML tags.
        """
        skill = self._skills.get(name)
        if not skill:
            available = ", ".join(self._skills.keys())
            return f"Error: Unknown skill '{name}'. Available: {available}"

        return skill.full_content

    def list_skills(self) -> list[dict[str, Any]]:
        """List all skills with metadata."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "tags": s.tags,
                "path": s.path,
                "size": len(s.body),
            }
            for s in self._skills.values()
        ]

    def create_skill_template(self, name: str, description: str, tags: list[str]) -> str:
        """Create a new skill template file."""
        skill_path = self.skills_dir / f"{name}.md"

        if skill_path.exists():
            return f"Error: Skill '{name}' already exists at {skill_path}"

        template = f"""---
description: {description}
tags: {json.dumps(tags)}
---

# {name}

## Purpose
Brief description of what this skill provides.

## When to Use
- Scenario 1
- Scenario 2

## Guidelines
1. Step one
2. Step two
3. Step three

## Examples

### Example 1: Description
```
Example code or output
```

### Example 2: Description
```
Example code or output
```

## Anti-patterns
- Don't do X
- Avoid Y

## Related
- Related skill 1
- Related skill 2
"""

        skill_path.write_text(template, encoding="utf-8")
        return f"Created skill template at {skill_path}"


class SkillToolInterface:
    """Tool interface for skill system."""

    def __init__(self, skill_loader: SkillLoader) -> None:
        self.loader = skill_loader

    def load_skill(self, name: str) -> dict[str, Any]:
        """Tool: Load a skill by name."""
        content = self.loader.load_skill_content(name)

        if content.startswith("Error:"):
            return {
                "ok": False,
                "error": content,
                "available_skills": [s.name for s in self.loader._skills.values()],
            }

        return {
            "ok": True,
            "skill_name": name,
            "content": content,
            "loaded_at": time.time(),
        }

    def list_skills(self) -> dict[str, Any]:
        """Tool: List all available skills."""
        skills = self.loader.list_skills()
        return {
            "ok": True,
            "skills": skills,
            "count": len(skills),
        }

    def get_skill_metadata(self, name: str) -> dict[str, Any]:
        """Tool: Get metadata for a specific skill."""
        skill = self.loader.get_skill(name)
        if not skill:
            return {"ok": False, "error": f"Skill '{name}' not found"}

        return {
            "ok": True,
            "name": skill.name,
            "description": skill.description,
            "tags": skill.tags,
            "meta": skill.meta,
            "size": len(skill.body),
        }


# Pre-built skill templates for common use cases
DEFAULT_SKILLS = {
    "python-refactor": """---
description: Python code refactoring patterns and best practices
tags: [python, refactoring]
---

# Python Refactoring

## When to Use
- Improving code readability
- Reducing complexity
- Applying design patterns

## Patterns

### Extract Function
```python
# Before
def process(data):
    result = []
    for item in data:
        if item > 0:
            transformed = item * 2
            result.append(transformed)
    return result

# After
def process(data):
    return [transform_positive(item) for item in data]

def transform_positive(item):
    return item * 2 if item > 0 else item
```

### Replace Conditional with Polymorphism
Use when multiple conditionals check the same type.

### Introduce Parameter Object
Use when multiple parameters always travel together.

## Anti-patterns
- Don't extract too early (premature abstraction)
- Don't leave dead code after refactoring
- Don't break tests without updating them
""",
    "security-review": """---
description: Security review checklist for code
tags: [security, review]
---

# Security Review

## Input Validation
- [ ] All user inputs are validated
- [ ] SQL injection prevented (parameterized queries)
- [ ] XSS prevented (output encoding)
- [ ] Command injection prevented

## Authentication/Authorization
- [ ] Authentication required for sensitive operations
- [ ] Authorization checks in place
- [ ] Session management secure
- [ ] No hardcoded credentials

## Data Protection
- [ ] Sensitive data encrypted at rest
- [ ] TLS for data in transit
- [ ] Proper key management
- [ ] No secrets in logs

## Common Vulnerabilities
- [ ] No path traversal (safe_path checks)
- [ ] No race conditions in file operations
- [ ] Resource limits in place (timeouts, sizes)
- [ ] Rate limiting considered
""",
    "api-design": """---
description: RESTful API design guidelines
tags: [api, design, rest]
---

# API Design

## URL Structure
- Use nouns, not verbs: `/users` not `/getUsers`
- Plural resources: `/orders` not `/order`
- Hierarchy for relationships: `/users/123/orders`

## HTTP Methods
- GET: Read (idempotent)
- POST: Create
- PUT: Update (full replacement)
- PATCH: Partial update
- DELETE: Remove

## Response Codes
- 200: OK
- 201: Created
- 204: No Content
- 400: Bad Request
- 401: Unauthorized
- 403: Forbidden
- 404: Not Found
- 409: Conflict
- 422: Unprocessable Entity
- 500: Internal Server Error

## Request/Response Body
- Use JSON
- CamelCase keys
- Consistent error format:
  ```json
  {"error": "message", "code": "ERROR_CODE", "details": {}}
  ```
""",
    "code-review": """---
description: Code review checklist and best practices
tags: [review, quality, teamwork]
---

# Code Review

## Before Starting
- [ ] Understand the requirements and context
- [ ] Check related issues/tickets
- [ ] Review tests first (if TDD)

## Checklist

### Functionality
- [ ] Code meets requirements
- [ ] Edge cases handled
- [ ] Error handling appropriate
- [ ] No obvious bugs

### Code Quality
- [ ] Clear naming (functions, variables, classes)
- [ ] Single responsibility principle
- [ ] DRY (Don't Repeat Yourself)
- [ ] No dead code or comments

### Testing
- [ ] Unit tests cover happy path
- [ ] Edge cases tested
- [ ] Integration tests if needed
- [ ] Test names describe behavior

### Documentation
- [ ] Complex logic explained
- [ ] Public APIs documented
- [ ] README updated if needed

## Review Tone
- Be constructive, not critical
- Ask questions rather than dictate
- Suggest, don't demand
- Acknowledge good practices
""",
    "test-driven-development": """---
description: TDD practices and patterns
tags: [testing, tdd, methodology]
---

# Test-Driven Development

## The Cycle
1. **Red**: Write a failing test
2. **Green**: Write minimal code to pass
3. **Refactor**: Improve code without changing behavior

## Rules
- Write tests BEFORE implementation
- Tests should fail for the right reason
- Implementation should be minimal
- Refactor only with passing tests

## Test Structure (Arrange-Act-Assert)
```python
def test_user_can_login_with_valid_credentials():
    # Arrange
    user = create_user("alice", "secret123")
    
    # Act
    result = login("alice", "secret123")
    
    # Assert
    assert result.success is True
    assert result.user_id == user.id
```

## Anti-patterns
- Testing implementation details
- Tests depending on each other
- Slow/flaky tests
- Testing getters/setters without logic
""",
    "error-handling": """---
description: Error handling patterns and best practices
tags: [error-handling, reliability, patterns]
---

# Error Handling

## Principles
1. **Fail Fast**: Detect errors as early as possible
2. **Fail Loud**: Log errors with context
3. **Fail Safe**: Maintain system integrity

## Patterns

### Result Type
```python
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

@dataclass
class Result(Generic[T]):
    success: bool
    value: T | None = None
    error: str = ""
    
    @classmethod
    def ok(cls, value: T) -> "Result[T]":
        return cls(success=True, value=value)
    
    @classmethod
    def fail(cls, error: str) -> "Result[T]":
        return cls(success=False, error=error)
```

### Early Return
```python
def process_order(order):
    if not order.is_valid():
        return Result.fail("Invalid order")
    
    if not has_inventory(order.items):
        return Result.fail("Out of stock")
    
    # Process valid order
    return Result.ok(complete_order(order))
```

## Guidelines
- Use exceptions for exceptional cases
- Use result types for expected failures
- Always log with context (request_id, user_id)
- Never swallow exceptions silently
""",
    "git-workflow": """---
description: Git workflow best practices
tags: [git, workflow, collaboration]
---

# Git Workflow

## Branch Strategy
- `main`: Production-ready code
- `feature/*`: New features
- `bugfix/*`: Bug fixes
- `hotfix/*`: Urgent production fixes

## Commit Messages
```
type(scope): subject

body (optional)

footer (optional)
```

Types: feat, fix, docs, style, refactor, test, chore

## Best Practices
- Commit often, commit small
- Write descriptive messages
- Use present tense ("Add feature" not "Added feature")
- Reference issues (#123)
- Rebase feature branches before merge
- Squash trivial commits

## Code Review Integration
- Create PR from feature branch
- Ensure CI passes
- Require 1+ approvals
- Squash and merge to main
""",
    "documentation-writing": """---
description: Technical documentation writing guidelines
tags: [docs, writing, communication]
---

# Documentation Writing

## Principles
1. **Audience-aware**: Write for the reader
2. **Purpose-driven**: Every doc has a goal
3. **Maintainable**: Easy to keep updated

## Types

### README
- Project name and description
- Installation instructions
- Quick start guide
- Usage examples
- Contributing guidelines

### API Documentation
- Endpoint description
- Request/response examples
- Error codes
- Authentication requirements

### Code Comments
- Explain WHY, not WHAT
- Document complex algorithms
- Warn about side effects
- TODO/FIXME with issue references

## Style
- Use active voice
- Be concise
- Use examples
- Keep it scannable (headers, lists)
- Update when code changes
""",
}


def install_default_skills(
    workspace: str,
    *,
    skills_root: str | None = None,
    explicit: bool = False,
) -> list[str]:
    """Install default skills to workspace.

    为防止隐式安装掩盖环境问题，默认拒绝执行，必须显式声明 explicit=True。

    Args:
        workspace: 工作区路径
        skills_root: 技能目录根路径（可选，默认从 KERNELONE_SKILLS_ROOT 读取）
        explicit: 必须为 True 才执行
    """
    if not explicit:
        raise RuntimeError("install_default_skills requires explicit=True to avoid implicit fallback behavior")
    skills_root_str = skills_root or _DEFAULT_SKILLS_ROOT
    workspace_skills_dir = Path(workspace) / skills_root_str
    runtime_skills_dir = Path(resolve_runtime_path(workspace, "runtime/skills"))
    workspace_skills_dir.mkdir(parents=True, exist_ok=True)
    runtime_skills_dir.mkdir(parents=True, exist_ok=True)

    installed = []
    for name, content in DEFAULT_SKILLS.items():
        workspace_skill_path = workspace_skills_dir / f"{name}.md"
        runtime_skill_path = runtime_skills_dir / f"{name}.md"
        if not workspace_skill_path.exists():
            workspace_skill_path.write_text(content, encoding="utf-8")
            installed.append(name)
        if not runtime_skill_path.exists():
            runtime_skill_path.write_text(content, encoding="utf-8")

    return installed
