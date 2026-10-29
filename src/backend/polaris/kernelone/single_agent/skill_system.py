"""Two-layer skill loading system.

Phase 4 implementation from learn-claude-code integration.
Layer 1: Skill metadata in system prompt (~100 tokens/skill)
Layer 2: Full skill body via load_skill tool result

设计约束：
- KernelOne 通用技能加载系统，不嵌入特定产品目录名
- skills_root 由调用方注入，默认从环境变量或 "<metadata>/skills" 读取
- 读取只读：SkillLoader 不创建目录、不复制文件、不写入任何内容
- 写入只允许显式 install_default_skills(...)，且必须经 KFS 落在工作区内
- 内置技能内容始终可读，不需要任何磁盘写入
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name
from polaris.kernelone.errors import PathSecurityError
from polaris.kernelone.fs.guarded_metadata import GuardedMetadataError

if TYPE_CHECKING:  # pragma: no cover - typing only
    # The concrete runtime module is imported for typing only: the package
    # facade resolves ``KernelFileSystem`` dynamically, which would erase
    # the real return types under a type checker.
    from polaris.kernelone.fs.contracts import KernelFileSystemAdapter
    from polaris.kernelone.fs.runtime import KernelFileSystem

logger = logging.getLogger(__name__)


class SkillWorkspaceMissingError(PathSecurityError, RuntimeError):
    """The workspace root does not exist, so no installation can be bound.

    The managed create capability provisions the *intermediate* directories
    of a target leaf, but the namespace root itself is retained, never
    provisioned.  This error reports that unsupported edge honestly instead
    of creating the root through an out-of-band path that would bypass the
    registered adapter.
    """

    def __init__(self, message: str, *, path: str = "") -> None:
        PathSecurityError.__init__(self, message, path=path, violation_type="skills_workspace_missing")


class SkillRootSecurityError(PathSecurityError, RuntimeError):
    """A requested skills root is not usable inside the workspace.

    The error is both a :class:`PathSecurityError` (semantic classification)
    and a :class:`RuntimeError` so that existing ``except RuntimeError``
    boundaries keep treating it as a controlled refusal instead of an
    unexpected crash.  It is always raised *before* any effect.
    """

    def __init__(self, message: str, *, path: str = "", violation_type: str = "skills_root_escape") -> None:
        PathSecurityError.__init__(self, message, path=path, violation_type=violation_type)


#: Upper bound of directory entries inspected during one discovery pass.
_MAX_SKILL_DISCOVERY_ENTRIES = 512

#: Upper bound of bytes written for one installed skill file.
_MAX_SKILL_FILE_BYTES = 1_048_576


def _canonical_skills_root() -> str:
    """Return the workspace-relative skills root inside the metadata tree."""
    metadata = str(get_workspace_metadata_dir_name() or "").strip() or ".polaris"
    return f"{metadata}/skills"


def _configured_skills_root() -> str:
    """Return the configured workspace-relative skills root.

    The environment variable is read per call so a host can relocate the root
    without reimporting this module.  The raw value is NOT trusted here; it is
    validated by :func:`_validated_skills_root` before any path is built.
    """
    raw = str(os.environ.get("KERNELONE_SKILLS_ROOT") or "").strip()
    return raw or _canonical_skills_root()


def _validated_skills_root(raw: str) -> str:
    """Return a canonical workspace-relative skills root or refuse it.

    The root is always resolved *inside* the workspace.  Absolute paths,
    ``..`` components, home expansion and drive-qualified paths are refused
    before any effect, so a hostile configuration can never redirect an
    installation outside the workspace.

    Raises:
        PathSecurityError: The requested root escapes the workspace or is
            not a usable workspace-relative path.
    """
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        raise SkillRootSecurityError(
            "skills root must not be empty",
            path=text,
            violation_type="skills_root_invalid",
        )
    if text.startswith("/") or text.startswith("~"):
        raise SkillRootSecurityError(
            "skills root must be workspace-relative",
            path=text,
            violation_type="skills_root_escape",
        )
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if not parts:
        raise SkillRootSecurityError(
            "skills root must not be empty",
            path=text,
            violation_type="skills_root_invalid",
        )
    if any(part == ".." for part in parts):
        raise SkillRootSecurityError(
            "skills root must not escape the workspace",
            path=text,
            violation_type="skills_root_escape",
        )
    if ":" in parts[0]:
        raise SkillRootSecurityError(
            "skills root must not be drive-qualified",
            path=text,
            violation_type="skills_root_escape",
        )
    return "/".join(parts)


def _legacy_runtime_skills_root() -> str:
    """Return the read-only legacy runtime skills root.

    Older releases installed a duplicate copy below the canonical runtime
    root.  That tree is now discovery-only: it is never written and never
    copied into the canonical root.
    """
    metadata = str(get_workspace_metadata_dir_name() or "").strip() or ".polaris"
    return f"{metadata}/runtime/skills"


def _kernel_fs_for(workspace: str) -> KernelFileSystem:
    """Build the workspace-bound KernelOne filesystem boundary."""
    from polaris.kernelone.fs import get_default_adapter
    from polaris.kernelone.fs.runtime import KernelFileSystem as _KernelFileSystem

    adapter = cast("KernelFileSystemAdapter", get_default_adapter())
    return _KernelFileSystem(str(Path(workspace).resolve()), adapter)


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

    The loader is strictly read-only: it never creates a directory, never
    copies a file and never writes content.  Skills are discovered from the
    canonical workspace skills root, from the read-only legacy runtime root
    (explicit discovery only) and from the built-in ``DEFAULT_SKILLS``
    catalogue, so a fresh workspace still exposes usable builtin skills
    without any disk effect.
    """

    MAX_SKILL_SIZE = 50000  # Max chars per skill

    def __init__(self, workspace: str, *, skills_root: str | None = None) -> None:
        self.workspace = Path(workspace)
        if skills_root is None:
            # A hostile *configuration* must not break the read path: the
            # loader is read-only, so falling back to the canonical root is
            # safe and keeps the builtin catalogue available.
            self.skills_root = self._configured_or_canonical_root()
        else:
            # An explicitly requested root is a caller decision and is
            # refused loudly when it is unusable.
            self.skills_root = _validated_skills_root(skills_root)
        self.skills_dir = self.workspace / self.skills_root
        self._kernel_fs: KernelFileSystem | None = None
        self._skills: dict[str, Skill] = {}
        self._load_all_skills()

    @staticmethod
    def _configured_or_canonical_root() -> str:
        """Return the configured skills root, or the canonical one when unusable."""
        try:
            return _validated_skills_root(_configured_skills_root())
        except SkillRootSecurityError as exc:
            logger.warning(
                "[SkillLoader] Ignoring unusable configured skills root %s: %s",
                exc.details.get("path", ""),
                exc,
            )
            return _canonical_skills_root()

    def _fs(self) -> KernelFileSystem:
        """Return the workspace-bound KernelOne filesystem boundary."""
        if self._kernel_fs is None:
            self._kernel_fs = _kernel_fs_for(str(self.workspace))
        return self._kernel_fs

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

    def _build_skill(self, name: str, text: str, path: str) -> Skill:
        """Build one skill from raw markdown content."""
        if len(text) > self.MAX_SKILL_SIZE:
            text = text[: self.MAX_SKILL_SIZE] + "\n... [truncated]"

        meta, body = self._parse_frontmatter(text)
        return Skill(
            name=name,
            description=meta.get("description", "No description"),
            tags=meta.get("tags", []) if isinstance(meta.get("tags"), list) else [],
            body=body,
            meta=meta,
            path=path,
        )

    def _read_workspace_skill(self, relative_path: str) -> str | None:
        """Read one workspace skill through the guarded KFS boundary.

        Returns the UTF-8 content, or ``None`` when the file is absent or
        cannot be read as a regular file.  A read failure never aborts the
        whole discovery pass.
        """
        try:
            return self._fs().workspace_read_text(relative_path, encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(
                "[SkillLoader] Failed to read %s: %s",
                relative_path,
                exc,
            )
            return None

    def _discover_root(self, root: str) -> None:
        """Load every ``*.md`` skill below one workspace-relative root."""
        try:
            listing = self._fs().workspace_list_directory(root, max_entries=_MAX_SKILL_DISCOVERY_ENTRIES)
        except GuardedMetadataError as exc:
            if exc.code == "guarded_metadata_missing":
                return
            logger.warning("[SkillLoader] Failed to list %s: %s", root, exc)
            return
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("[SkillLoader] Failed to list %s: %s", root, exc)
            return

        for entry in listing.entries:
            if not entry.name.endswith(".md"):
                continue
            relative = f"{root}/{entry.name}"
            content = self._read_workspace_skill(relative)
            if content is None:
                continue
            self._skills.setdefault(entry.name[:-3], self._build_skill(entry.name[:-3], content, relative))

    def _load_all_skills(self) -> None:
        """Load workspace skills, then fall back to the builtin catalogue."""
        self._discover_root(self.skills_root)
        # The legacy runtime tree is discovery-only: it is read, never written
        # and never copied into the canonical root.
        self._discover_root(_legacy_runtime_skills_root())

        for name, content in DEFAULT_SKILLS.items():
            if name in self._skills:
                continue
            self._skills[name] = self._build_skill(name, content, f"builtin://{name}")

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
    """Install default skills into the workspace skills root.

    为防止隐式安装掩盖环境问题，默认拒绝执行，必须显式声明 explicit=True。

    The installation is workspace-contained by construction:

    * the skills root is validated as a workspace-relative path *before* any
      effect, so an absolute, home-expanded, drive-qualified or ``..``
      escaping root is refused without touching the filesystem;
    * every file is created through the *active adapter* via the generic
      ``KernelFileSystem.workspace_create_text`` capability.  The effect is
      owned by the adapter, so a registered refusal stops the installation
      before any parent directory or file exists.  The legacy writer is
      never used as an authorization probe;
    * a single canonical root is written — the legacy runtime tree is never
      written again;
    * an existing file is never overwritten, so user-authored skill content
      is preserved.

    Args:
        workspace: 工作区路径
        skills_root: 技能目录根路径（可选，默认从 KERNELONE_SKILLS_ROOT 读取）
        explicit: 必须为 True 才执行

    Returns:
        The names of the skills created by this call.  Skills that already
        existed are not reported and not rewritten.

    Raises:
        RuntimeError: ``explicit`` was not True.
        SkillRootSecurityError: The requested skills root is not usable
            inside the workspace.  Raised before any effect.
        NamespaceOperationUnavailableError: The registered adapter cannot
            perform the managed create.  Raised before any effect.
        NamespaceBoundaryError: The target leaf is not a usable workspace
            path (linked parent, non-regular leaf, oversized content).
        NamespaceOperationEffectUnknownError: The create may already have
            landed.  It is never replayed and never rolled back.
    """
    if not explicit:
        raise RuntimeError("install_default_skills requires explicit=True to avoid implicit fallback behavior")

    requested_root = skills_root if skills_root is not None else _configured_skills_root()
    root = _validated_skills_root(requested_root)

    kernel_fs = _kernel_fs_for(workspace)
    _require_existing_workspace(kernel_fs)

    installed: list[str] = []
    for name, content in DEFAULT_SKILLS.items():
        relative_path = f"{root}/{name}.md"
        if _create_skill_file(kernel_fs, relative_path, content):
            installed.append(name)

    return installed


def _require_existing_workspace(kernel_fs: KernelFileSystem) -> None:
    """Require an existing workspace root before any installation effect.

    The managed create capability creates the *intermediate* directories of
    the target leaf, but it cannot create the workspace root itself: the
    namespace root is retained, not provisioned.  Installing into a
    workspace that does not exist yet is therefore reported honestly as an
    unsupported edge instead of being satisfied with an out-of-band
    directory creation that would bypass the registered adapter.
    """
    if kernel_fs.workspace_exists("."):
        return
    raise SkillWorkspaceMissingError(
        f"skill installation requires an existing workspace root: {kernel_fs.workspace}",
        path=str(kernel_fs.workspace),
    )


def _create_skill_file(kernel_fs: KernelFileSystem, relative_path: str, content: str) -> bool:
    """Create one skill file when it is absent, through the active adapter.

    Returns True when this call created the file, False when it already
    existed.  An existing file is never replaced, so user-authored skill
    content survives an installation.

    The create capability creates the missing intermediate directories of
    the leaf itself, so no directory is provisioned before the adapter has
    accepted the operation: a registered refusal leaves no physical trace.
    """
    receipt = kernel_fs.workspace_create_text(
        relative_path,
        content,
        encoding="utf-8",
        max_bytes=max(len(content.encode("utf-8")), _MAX_SKILL_FILE_BYTES),
    )
    return bool(receipt.created)
