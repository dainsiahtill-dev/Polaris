"""Prompt profile selection for language and task focused role prompts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_USER_PROFILE_DIR = ".polaris/prompt_profiles"
_PROFILE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_PATH_TOKEN_RE = re.compile(r"(?P<path>[A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]{1,12})")
_MAX_TEMPLATE_CHARS = 6000

_EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".c": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".h": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".vue": "vue",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".html": "html_css",
    ".css": "html_css",
    ".scss": "html_css",
    ".sql": "sql",
}

_SPECIAL_FILE_LANGUAGE_MAP: dict[str, str] = {
    "package.json": "typescript",
    "tsconfig.json": "typescript",
    "vite.config.ts": "typescript",
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "cargo.toml": "rust",
    "go.mod": "go",
    "cmakelists.txt": "cpp",
    "pom.xml": "java",
}

_TASK_ALIASES: dict[str, str] = {
    "new_code": "implement",
    "new_feature": "implement",
    "code_generation": "implement",
    "file_creation": "implement",
    "generation": "implement",
    "bootstrap": "implement",
    "bug_fix": "bugfix",
    "fix": "bugfix",
    "repair": "bugfix",
    "code_review": "review",
    "qa": "verify",
    "quality_gate": "verify",
}

_LANGUAGE_FOCUS: dict[str, str] = {
    "python": (
        "Use senior Python engineering standards: PEP 8, clear pathlib/resource handling, "
        "complete type annotations, explicit exceptions, pytest-friendly pure core logic, "
        "and logging/configuration boundaries instead of hidden side effects."
    ),
    "typescript": (
        "Use strict TypeScript standards: explicit domain types, no implicit any, coherent "
        "module exports/imports, npm scripts that run locally, and browser/Node entrypoints "
        "that match the compiled output."
    ),
    "javascript": (
        "Use maintainable JavaScript standards: small modules, explicit runtime guards, "
        "portable npm scripts, deterministic errors, and tests that exercise public behavior."
    ),
    "node": (
        "Use Node.js production standards: package scripts must map to real local entrypoints, "
        "I/O must be isolated from core logic, process exits must be intentional, and errors "
        "must include actionable context."
    ),
    "react": (
        "Use React engineering standards: typed props/state, stable component boundaries, "
        "accessible controls, deterministic rendering, and tests focused on user-visible behavior."
    ),
    "vue": (
        "Use Vue engineering standards: typed component contracts, explicit state ownership, "
        "clear composables, scoped styling, and tests around public component behavior."
    ),
    "go": (
        "Use Go engineering standards: small packages, explicit errors, context-aware I/O, "
        "table-driven tests, gofmt-compatible code, and no hidden global mutable state."
    ),
    "cpp": (
        "Use C++17 engineering standards: coherent header/source contracts, source-relative "
        "quote includes, consistent namespaces across .hpp/.cpp/main, explicit build commands, "
        "and tests that compile real translation units instead of only checking file presence."
    ),
    "rust": (
        "Use Rust engineering standards: precise ownership, Result-based error flow, small "
        "modules, cargo-friendly layout, and tests that validate behavior instead of internals."
    ),
    "java": (
        "Use Java engineering standards: cohesive classes, explicit interfaces, checked boundary "
        "conditions, build-tool compatible layout, and tests for public behavior and edge cases."
    ),
    "kotlin": (
        "Use Kotlin engineering standards: null-safety, cohesive services, explicit sealed/error "
        "models where useful, build-tool compatible layout, and focused tests."
    ),
    "shell": (
        "Use shell scripting standards: set strict mode where appropriate, quote expansions, "
        "validate inputs, make commands idempotent, and avoid destructive defaults."
    ),
    "html_css": (
        "Use web standards: valid HTML entrypoints, accessible markup, stable responsive layout, "
        "CSS with clear ownership, and assets/scripts that resolve after build."
    ),
    "sql": (
        "Use SQL engineering standards: explicit schema assumptions, reversible migrations where "
        "applicable, indexed query paths, and tests/fixtures that show expected data behavior."
    ),
    "generic": (
        "Use production engineering standards: high cohesion, low coupling, clear boundaries, "
        "typed or schema-backed interfaces, explicit errors, and testable behavior."
    ),
}

_TASK_FOCUS: dict[str, str] = {
    "implement": (
        "For implementation tasks, deliver complete runnable artifacts: files on disk, dependency "
        "setup, at least one real gate, and one real CLI/Web/API entrypoint."
    ),
    "refactor": (
        "For refactor tasks, preserve external behavior, keep the change scoped, remove only real "
        "complexity, and verify with regression-oriented gates."
    ),
    "bugfix": (
        "For bugfix tasks, explain the failing behavior through code changes: fix the root cause, "
        "add or preserve regression coverage, and avoid hard-coded success paths."
    ),
    "review": (
        "For review tasks, prioritize correctness, regressions, security, missing tests, and "
        "maintainability risks before style comments."
    ),
    "test": (
        "For test tasks, test public behavior rather than implementation details, cover happy path, "
        "edge cases, invalid input, and regression risks with deterministic fixtures."
    ),
    "verify": (
        "For verification tasks, run a real physical gate, record the command and result, and "
        "distinguish baseline failures from implementation defects."
    ),
    "audit": (
        "For audit tasks, treat runtime events, provider request snapshots, receipts, logs, and "
        "gate output as evidence; do not close on claims alone."
    ),
}

_ARTIFACT_FOCUS: dict[str, str] = {
    "cli": "CLI artifacts must expose a real command that exits correctly and reports actionable errors.",
    "web": "Web artifacts must have a real browser entrypoint whose scripts/assets resolve after build.",
    "html5_canvas": (
        "HTML5/Canvas artifacts require a browser-specific bootstrap: initialize after the DOM/canvas exists, "
        "paint a visible non-empty first frame before user interaction, keep canvas dimensions stable, and make "
        "the HTML script point to browser-loadable code rather than a Node-only CLI entrypoint."
    ),
    "api": "API artifacts must expose a real route or handler with explicit request/response contracts.",
    "library": "Library artifacts must expose stable public APIs and keep I/O outside core logic.",
    "test_suite": "Test artifacts must be executable by the declared package/test runner.",
    "config": "Config artifacts must match the package manager, compiler, and runtime entrypoints.",
    "docs": "Docs artifacts must describe real behavior and commands that exist in the workspace.",
}

_FORBIDDEN_USER_TEMPLATE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bskip\s+(lint|test|tests|typecheck|type\s*check|gate|verification)\b",
        r"\bignore\s+(errors?|failures?|exceptions?)\b",
        r"\bdisable\s+(lint|test|tests|typecheck|guard|gate)\b",
        r"\breturn\s+true\b",
        r"\bhard\s*-?\s*code\s+(success|pass|true)\b",
        r"\bswallow\s+(errors?|exceptions?)\b",
        r"\bdelete\s+(tests?|assertions?)\b",
        r"\bbypass\s+(guard|gate|policy|auth|permission)\b",
        r"吞掉异常|硬编码成功|删除测试|跳过测试|绕过门禁|跳过门禁",
    )
)


@dataclass(frozen=True)
class PromptProfileTemplate:
    """One language/task/stage prompt profile fragment."""

    id: str
    content: str
    language: str = "any"
    task_type: str = "any"
    role: str = "any"
    stage: str = "any"
    artifact: str = "any"
    source: str = "builtin"


@dataclass(frozen=True)
class RejectedPromptProfile:
    """A rejected user profile with a deterministic reason."""

    id: str
    path: str
    reason: str


@dataclass(frozen=True)
class DisabledPromptProfile:
    """A user-disabled profile id."""

    id: str
    path: str


@dataclass(frozen=True)
class PromptProfileSelection:
    """Selected prompt profiles and audit metadata."""

    templates: tuple[PromptProfileTemplate, ...]
    inferred_language: str
    inferred_task_type: str
    inferred_stage: str
    inferred_artifact: str
    explicit_ids: tuple[str, ...] = ()
    rejected_user_templates: tuple[RejectedPromptProfile, ...] = ()
    disabled_user_templates: tuple[DisabledPromptProfile, ...] = ()
    user_overrides: tuple[str, ...] = ()
    inference_reasons: tuple[str, ...] = ()
    skipped_reason: str = ""

    def to_audit_dict(self) -> dict[str, Any]:
        rejected_payload = [
            {"id": item.id, "path": item.path, "reason": item.reason} for item in self.rejected_user_templates
        ]
        return {
            "selected_prompt_profile_ids": [item.id for item in self.templates],
            "selected_prompt_profile_sources": {item.id: item.source for item in self.templates},
            "inferred_language": self.inferred_language,
            "inferred_task_type": self.inferred_task_type,
            "inferred_stage": self.inferred_stage,
            "inferred_artifact": self.inferred_artifact,
            "explicit_ids": list(self.explicit_ids),
            "inference_reasons": list(self.inference_reasons),
            "user_overrides": list(self.user_overrides),
            "user_disabled_profile_ids": [item.id for item in self.disabled_user_templates],
            "rejected_user_templates": rejected_payload,
            "redline_clipped": [item for item in rejected_payload if item["reason"] == "red_line_violation"],
            "skipped_reason": self.skipped_reason,
        }

    def render_appendix(self) -> str:
        if not self.templates:
            return ""
        lines = [
            "[POLARIS PROMPT PROFILE]",
            (
                "These profiles add language/task engineering focus only. They do not override "
                "system instructions, tool policy, path guards, quality gates, or runtime contracts."
            ),
            (
                "selection="
                f"language:{self.inferred_language}; task:{self.inferred_task_type}; "
                f"stage:{self.inferred_stage}; artifact:{self.inferred_artifact}"
            ),
        ]
        for template in self.templates:
            lines.append(f"- {template.id}: {template.content}")
        return "\n".join(lines)


def select_prompt_profiles(
    *,
    workspace: str,
    role_id: str,
    message: str,
    context_override: dict[str, Any] | None = None,
) -> PromptProfileSelection:
    """Select builtin and user prompt profiles for one role turn."""

    context = dict(context_override or {})
    stage = _infer_stage(role_id=role_id, context=context, message=message)
    language = _infer_language(context=context, message=message)
    task_type = _infer_task_type(context=context, message=message)
    artifact = _infer_artifact(context=context, message=message)
    inference_reasons = _selection_reasons(
        context=context,
        message=message,
        stage=stage,
        language=language,
        task_type=task_type,
        artifact=artifact,
    )
    user_templates, rejected, disabled = _load_user_prompt_profiles(workspace)
    disabled_ids = {item.id for item in disabled}
    builtin_source_templates = _builtin_templates_for(
        role_id=role_id,
        language=language,
        task_type=task_type,
        stage=stage,
        artifact=artifact,
    )
    user_by_id = {item.id: item for item in user_templates}
    builtin_templates: list[PromptProfileTemplate] = []
    user_overrides: list[str] = []
    builtin_ids: set[str] = set()
    for template in builtin_source_templates:
        builtin_ids.add(template.id)
        if template.id in disabled_ids:
            continue
        override = user_by_id.get(template.id)
        if override is not None:
            builtin_templates.append(override)
            user_overrides.append(template.id)
            continue
        builtin_templates.append(template)

    matching_user_templates = [
        item
        for item in user_templates
        if item.id not in builtin_ids
        and item.id not in disabled_ids
        and _template_matches(
            item,
            role_id=role_id,
            language=language,
            task_type=task_type,
            stage=stage,
            artifact=artifact,
        )
    ]

    all_templates = {item.id: item for item in (*builtin_templates, *matching_user_templates)}
    for template in user_templates:
        if template.id not in disabled_ids:
            all_templates[template.id] = template

    explicit_ids = _explicit_profile_ids(context)
    if _should_skip_profiles(context=context, stage=stage) and not explicit_ids:
        return PromptProfileSelection(
            templates=(),
            inferred_language=language,
            inferred_task_type=task_type,
            inferred_stage=stage,
            inferred_artifact=artifact,
            rejected_user_templates=tuple(rejected),
            disabled_user_templates=tuple(disabled),
            user_overrides=tuple(user_overrides),
            inference_reasons=tuple(inference_reasons),
            skipped_reason="strict_quality_repair",
        )

    if explicit_ids:
        selected = [all_templates[item] for item in explicit_ids if item in all_templates]
    else:
        selected = [*builtin_templates, *matching_user_templates]

    selected = _dedupe_templates(selected)
    return PromptProfileSelection(
        templates=tuple(selected),
        inferred_language=language,
        inferred_task_type=task_type,
        inferred_stage=stage,
        inferred_artifact=artifact,
        explicit_ids=tuple(explicit_ids),
        rejected_user_templates=tuple(rejected),
        disabled_user_templates=tuple(disabled),
        user_overrides=tuple(user_overrides),
        inference_reasons=tuple(inference_reasons),
    )


def build_prompt_profile_appendix(
    *,
    workspace: str,
    role_id: str,
    message: str,
    context_override: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return rendered prompt profile appendix and machine-readable audit metadata."""

    selection = select_prompt_profiles(
        workspace=workspace,
        role_id=role_id,
        message=message,
        context_override=context_override,
    )
    return selection.render_appendix(), selection.to_audit_dict()


def _builtin_templates_for(
    *,
    role_id: str,
    language: str,
    task_type: str,
    stage: str,
    artifact: str,
) -> tuple[PromptProfileTemplate, ...]:
    role_token = _normalize_token(role_id, fallback="role")
    language_token = language if language in _LANGUAGE_FOCUS else "generic"
    task_token = task_type if task_type in _TASK_FOCUS else "implement"
    templates = [
        PromptProfileTemplate(
            id=f"builtin.language.{language_token}",
            language=language_token,
            content=_LANGUAGE_FOCUS[language_token],
        ),
        PromptProfileTemplate(
            id=f"builtin.task.{task_token}",
            task_type=task_token,
            content=_TASK_FOCUS[task_token],
        ),
        PromptProfileTemplate(
            id=f"builtin.role_stage.{role_token}.{stage}",
            role=role_token,
            stage=stage,
            content=_role_stage_focus(role_token, stage),
        ),
    ]
    if artifact in _ARTIFACT_FOCUS:
        templates.append(
            PromptProfileTemplate(
                id=f"builtin.artifact.{artifact}",
                artifact=artifact,
                content=_ARTIFACT_FOCUS[artifact],
            )
        )
    return tuple(templates)


def _template_matches(
    template: PromptProfileTemplate,
    *,
    role_id: str,
    language: str,
    task_type: str,
    stage: str,
    artifact: str,
) -> bool:
    """Return whether a user template should auto-attach for this turn."""

    dimensions = {
        "role": _normalize_token(role_id, fallback="role"),
        "language": language,
        "task_type": task_type,
        "stage": stage,
        "artifact": artifact,
    }
    template_values = {
        "role": template.role,
        "language": template.language,
        "task_type": template.task_type,
        "stage": template.stage,
        "artifact": template.artifact,
    }
    if all(value == "any" for value in template_values.values()):
        return False
    for key, expected in dimensions.items():
        actual = template_values[key]
        if actual != "any" and actual != expected:
            return False
    return True


def _selection_reasons(
    *,
    context: dict[str, Any],
    message: str,
    stage: str,
    language: str,
    task_type: str,
    artifact: str,
) -> list[str]:
    reasons: list[str] = []
    if _explicit_language_from_context(context):
        reasons.append(f"language:explicit:{language}")
    elif _language_from_contract_text(message):
        reasons.append(f"language:contract:{language}")
    elif _path_candidates(context, message):
        reasons.append(f"language:path:{language}")
    else:
        reasons.append(f"language:message:{language}")
    if _first_string(context.get("task_type"), context.get("operation"), context.get("intent")):
        reasons.append(f"task_type:explicit:{task_type}")
    else:
        reasons.append(f"task_type:message:{task_type}")
    if _first_string(context.get("artifact"), context.get("artifact_type"), context.get("project_kind")):
        reasons.append(f"artifact:explicit:{artifact}")
    else:
        reasons.append(f"artifact:inferred:{artifact}")
    reasons.append(f"stage:inferred:{stage}")
    return reasons


def _role_stage_focus(role_id: str, stage: str) -> str:
    if role_id == "chief_engineer":
        return (
            "Chief Engineer must produce a concrete blueprint with module boundaries, contracts, "
            "gate commands, and handoff evidence for Director."
        )
    if role_id == "director":
        if stage == "materialize":
            return (
                "Director materialization must be tool-first: write complete UTF-8 files, prepare "
                "dependencies, run at least one real gate, and execute one real entrypoint."
            )
        if stage == "quality_repair":
            return (
                "Director repair must obey the repair contract exactly, stay scoped to the failed "
                "target set, use the provided gate errors as the source of truth, and avoid "
                "rewriting unrelated files."
            )
        return (
            "Director must implement through authorized tools, keep mutations scoped, and leave "
            "runtime evidence for files, gates, and entrypoints."
        )
    if role_id == "qa":
        return "QA must verify physical evidence: files, dependency setup, gate output, runtime entrypoint, and logs."
    if role_id == "pm":
        return "PM must produce executable contracts with scope, target files, acceptance gates, and no prompt leakage."
    return "The role must keep responsibilities scoped and preserve platform red lines."


def _explicit_profile_ids(context: dict[str, Any]) -> list[str]:
    raw = (
        context.get("prompt_profile_ids")
        or context.get("prompt_profiles")
        or context.get("prompt_profile")
        or context.get("prompt_profile_id")
    )
    if raw is None:
        metadata = context.get("metadata")
        if isinstance(metadata, dict):
            raw = (
                metadata.get("prompt_profile_ids")
                or metadata.get("prompt_profiles")
                or metadata.get("prompt_profile")
                or metadata.get("prompt_profile_id")
            )
    if raw is None:
        return []
    values = raw if isinstance(raw, list | tuple | set) else [raw]
    return [
        token
        for item in values
        if (token := str(item or "").strip()) and token.lower() not in {"default", "auto", "builtin"}
    ]


def _should_skip_profiles(*, context: dict[str, Any], stage: str) -> bool:
    del context, stage
    return False


def _infer_stage(*, role_id: str, context: dict[str, Any], message: str) -> str:
    explicit = _first_string(
        context.get("prompt_stage"),
        context.get("stage"),
        context.get("phase"),
    )
    if explicit:
        return _normalize_token(explicit, fallback="default")
    if (
        isinstance(context.get("director_quality_repair"), dict)
        or isinstance(context.get("factory_workspace_quality_repair"), dict)
        or "materialization quality repair" in message.lower()
    ):
        return "quality_repair"
    delivery_mode = str(context.get("delivery_mode") or "").strip().lower()
    if delivery_mode == "materialize_changes" or "[mode:materialize]" in message.lower():
        return "materialize"
    role_token = _normalize_token(role_id, fallback="role")
    message_lower = message.lower()
    if role_token == "director" and any(
        token in message_lower
        for token in (
            "pm task contract /",
            "任务合同",
            "目标文件覆盖硬门禁",
            "请通过运行时正式写入工具完成修改",
        )
    ):
        return "materialize"
    if role_token == "chief_engineer":
        return "blueprint"
    if role_token == "pm":
        return "planning"
    if role_token == "qa":
        return "verify"
    return "default"


def _infer_language(*, context: dict[str, Any], message: str) -> str:
    explicit = _explicit_language_from_context(context)
    if explicit:
        return explicit

    contract_language = _language_from_contract_text(message)
    if contract_language:
        return contract_language

    candidates = _path_candidates(context, message)
    source_candidates = [
        path
        for path in candidates
        if not any(
            part in {"dist", "build", "out", "coverage", "node_modules"}
            for part in Path(path.strip().replace("\\", "/")).parts
        )
    ]
    inference_candidates = source_candidates or candidates
    scores: dict[str, int] = {}
    for path in inference_candidates:
        name = Path(path).name.lower()
        suffix = Path(path).suffix.lower()
        language = _SPECIAL_FILE_LANGUAGE_MAP.get(name) or _EXTENSION_LANGUAGE_MAP.get(suffix)
        if not language:
            continue
        scores[language] = scores.get(language, 0) + 1
    if scores:
        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[0][0]

    lowered = message.lower()
    keyword_map = {
        "python": "python",
        "typescript": "typescript",
        "react": "react",
        "vue": "vue",
        "javascript": "javascript",
        "node": "node",
        "golang": "go",
        "go_compile": "go",
        "c++": "cpp",
        "cpp": "cpp",
        "c++17": "cpp",
        "cpp_compile": "cpp",
        "rust": "rust",
        "java": "java",
        "shell": "shell",
        "bash": "shell",
        "html": "html_css",
        "css": "html_css",
        "sql": "sql",
    }
    for keyword, language in keyword_map.items():
        if keyword in lowered:
            return language
    return "generic"


def _infer_task_type(*, context: dict[str, Any], message: str) -> str:
    explicit = _first_string(
        context.get("task_type"),
        context.get("operation"),
        context.get("intent"),
    )
    if explicit:
        return _normalize_task_type(explicit)
    metadata = context.get("metadata")
    if isinstance(metadata, dict):
        explicit = _first_string(metadata.get("task_type"), metadata.get("operation"), metadata.get("intent"))
        if explicit:
            return _normalize_task_type(explicit)

    lowered = message.lower()
    if any(token in lowered for token in ("review", "审查", "代码审查")):
        return "review"
    if any(token in lowered for token in ("refactor", "重构")):
        return "refactor"
    if any(token in lowered for token in ("bug", "fix", "repair", "修复", "缺陷")):
        return "bugfix"
    if any(token in lowered for token in ("implement", "create", "generate", "scaffold", "实现", "创建", "生成")):
        return "implement"
    if any(token in lowered for token in ("pytest", "test", "测试", "spec")):
        return "test"
    if any(token in lowered for token in ("verify", "gate", "lint", "typecheck", "验证", "门禁")):
        return "verify"
    if any(token in lowered for token in ("audit", "审计")):
        return "audit"
    return "implement"


def _infer_artifact(*, context: dict[str, Any], message: str) -> str:
    explicit = _first_string(context.get("artifact"), context.get("artifact_type"), context.get("project_kind"))
    if explicit:
        return _normalize_artifact(explicit)
    candidates = [path.strip().lower() for path in _path_candidates(context, message) if path.strip()]
    lowered = message.lower()
    has_test = any(
        path.endswith((".test.ts", ".test.tsx", ".spec.ts", ".test.js", ".spec.js", "_test.py")) for path in candidates
    )
    has_config = any(
        Path(path).name.lower() in {"package.json", "tsconfig.json", "pyproject.toml"} for path in candidates
    )
    has_web = any(path.endswith((".html", ".css", ".tsx", ".jsx", ".vue")) for path in candidates)
    has_canvas_entry_path = any(
        path.endswith(("/web.ts", "/web.tsx", "/renderer.ts", "/renderer.tsx", "/simulation.ts", "/simulation.tsx"))
        or "canvas" in path
        for path in candidates
    )
    has_source = any(
        path.endswith(
            (
                ".py",
                ".pyi",
                ".ts",
                ".js",
                ".mjs",
                ".cjs",
                ".go",
                ".rs",
                ".java",
                ".kt",
                ".kts",
                ".sh",
                ".bash",
                ".zsh",
                ".sql",
            )
        )
        for path in candidates
    )
    has_canvas = any(
        token in lowered
        for token in (
            "html5 canvas",
            "canvas",
            "webgl",
            "2d context",
            "requestanimationframe",
            "non-empty canvas",
            "画布",
            "首帧",
        )
    )
    has_html5_canvas_text = has_canvas and any(
        token in lowered
        for token in (
            "index.html",
            "<html",
            "html5",
            "browser",
            "浏览器",
            "页面",
        )
    )
    if has_web and (has_canvas or has_canvas_entry_path):
        return "html5_canvas"
    if has_html5_canvas_text:
        return "html5_canvas"
    if any(token in lowered for token in ("cli", "command line", "命令行")):
        return "cli"
    if any(token in lowered for token in ("api", "rest", "http route", "接口")):
        return "api"
    if has_web or any(token in lowered for token in ("web", "browser", "frontend", "页面", "浏览器")):
        return "web"
    if has_test:
        return "test_suite"
    if any(token in lowered for token in ("readme", "docs", "文档")):
        return "docs"
    if has_source:
        return "library"
    if has_config:
        return "config"
    return "library"


def _path_candidates(context: dict[str, Any], message: str) -> list[str]:
    values: list[str] = []
    for key in (
        "target_files",
        "files",
        "changed_files",
        "repair_target_files",
        "missing_target_files",
        "runtime_smoke_target_files",
        "semantic_quality_target_files",
    ):
        raw = context.get(key)
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, list | tuple | set):
            values.extend(str(item) for item in raw if str(item or "").strip())
    quality_repair = context.get("director_quality_repair")
    if isinstance(quality_repair, dict):
        values.extend(_path_candidates(quality_repair, ""))
    factory_quality_repair = context.get("factory_workspace_quality_repair")
    if isinstance(factory_quality_repair, dict):
        values.extend(_path_candidates(factory_quality_repair, ""))
    values.extend(match.group("path") for match in _PATH_TOKEN_RE.finditer(str(message or "")))
    return _dedupe_strings(values)


def _load_user_prompt_profiles(
    workspace: str,
) -> tuple[list[PromptProfileTemplate], list[RejectedPromptProfile], list[DisabledPromptProfile]]:
    workspace_text = str(workspace or "").strip()
    if not workspace_text:
        return [], [], []
    base = Path(workspace_text).expanduser()
    profile_dir = base / _USER_PROFILE_DIR
    if not profile_dir.exists() or not profile_dir.is_dir():
        return [], [], []

    loaded: list[PromptProfileTemplate] = []
    rejected: list[RejectedPromptProfile] = []
    disabled: list[DisabledPromptProfile] = []
    for path in sorted(profile_dir.iterdir()):
        if path.suffix.lower() not in {".json", ".yaml", ".yml"} or not path.is_file():
            continue
        try:
            payload = _read_user_profile_payload(path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            rejected.append(RejectedPromptProfile(id=path.stem, path=str(path), reason=type(exc).__name__))
            continue
        entries = _profile_entries(payload)
        for index, entry in enumerate(entries):
            if entry.get("enabled") is False:
                disabled_id = str(entry.get("id") or f"user.{path.stem}.{index}").strip()
                if _PROFILE_ID_RE.fullmatch(disabled_id):
                    disabled.append(DisabledPromptProfile(id=disabled_id, path=str(path)))
                else:
                    rejected.append(
                        RejectedPromptProfile(id=disabled_id or path.stem, path=str(path), reason="invalid_id")
                    )
                continue
            template, reason = _coerce_user_template(entry, source_path=path, index=index)
            if template is None:
                rejected.append(
                    RejectedPromptProfile(
                        id=str(entry.get("id") or f"{path.stem}:{index}") if isinstance(entry, dict) else path.stem,
                        path=str(path),
                        reason=reason or "invalid_profile",
                    )
                )
                continue
            loaded.append(template)
    return loaded, rejected, disabled


def _read_user_profile_payload(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    try:
        import yaml
    except ImportError as exc:
        raise ValueError("yaml_unavailable") from exc
    return yaml.safe_load(text)


def _profile_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        raw_profiles = payload.get("profiles")
        if isinstance(raw_profiles, list):
            return [item for item in raw_profiles if isinstance(item, dict)]
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _coerce_user_template(
    entry: dict[str, Any],
    *,
    source_path: Path,
    index: int,
) -> tuple[PromptProfileTemplate | None, str]:
    profile_id = str(entry.get("id") or f"user.{source_path.stem}.{index}").strip()
    if not _PROFILE_ID_RE.fullmatch(profile_id):
        return None, "invalid_id"
    content = str(entry.get("content") or entry.get("text") or "").strip()
    if not content:
        return None, "empty_content"
    if len(content) > _MAX_TEMPLATE_CHARS:
        return None, "content_too_large"
    for pattern in _FORBIDDEN_USER_TEMPLATE_PATTERNS:
        if pattern.search(content):
            return None, "red_line_violation"
    return (
        PromptProfileTemplate(
            id=profile_id,
            content=content,
            language=_normalize_language(entry.get("language") or "any"),
            task_type=_normalize_task_type(entry.get("task_type") or "any"),
            role=_normalize_token(entry.get("role") or "any", fallback="any"),
            stage=_normalize_token(entry.get("stage") or "any", fallback="any"),
            artifact=_normalize_artifact(entry.get("artifact") or "any"),
            source=f"user:{source_path.name}",
        ),
        "",
    )


def _normalize_language(value: Any) -> str:
    token = _normalize_token(value, fallback="generic")
    aliases = {
        "ts": "typescript",
        "tsx": "typescript",
        "js": "javascript",
        "jsx": "javascript",
        "nodejs": "node",
        "golang": "go",
        "c++": "cpp",
        "cxx": "cpp",
        "bash": "shell",
        "sh": "shell",
        "html": "html_css",
        "css": "html_css",
        "web": "html_css",
    }
    return aliases.get(token, token)


def _normalize_task_type(value: Any) -> str:
    token = _normalize_token(value, fallback="implement")
    return _TASK_ALIASES.get(token, token)


def _normalize_artifact(value: Any) -> str:
    token = _normalize_token(value, fallback="library")
    aliases = {
        "frontend": "web",
        "browser": "web",
        "server": "api",
        "backend": "api",
        "tests": "test_suite",
        "test": "test_suite",
        "configuration": "config",
        "documentation": "docs",
        "doc": "docs",
    }
    return aliases.get(token, token)


def _explicit_language_from_context(context: dict[str, Any]) -> str:
    explicit = _first_string(
        context.get("language"),
        context.get("prompt_language"),
        context.get("programming_language"),
        context.get("primary_language"),
        context.get("main_language"),
        context.get("detected_language"),
    )
    if explicit:
        return _normalize_language(explicit)
    metadata = context.get("metadata")
    if isinstance(metadata, dict):
        tech_stack = metadata.get("tech_stack")
        if isinstance(tech_stack, dict):
            explicit = _first_string(tech_stack.get("language"))
            if explicit:
                return _normalize_language(explicit)
        explicit = _first_string(
            metadata.get("language"),
            metadata.get("prompt_language"),
            metadata.get("programming_language"),
            metadata.get("primary_language"),
            metadata.get("main_language"),
            metadata.get("detected_language"),
        )
        if explicit:
            return _normalize_language(explicit)
    return ""


def _language_from_contract_text(message: str) -> str:
    lowered = str(message or "").lower()
    explicit_patterns = (
        r"(?:主语言|主要语言|primary\s+language|main\s+language|programming\s+language|language)\s*[:：=-]\s*([a-z0-9+#._-]+)",
        r"(?:detected_language|primary_language|main_language|programming_language)\s*[:：=-]\s*([a-z0-9+#._-]+)",
    )
    for pattern in explicit_patterns:
        match = re.search(pattern, lowered, re.IGNORECASE)
        if not match:
            continue
        language = _normalize_language(match.group(1))
        if language in _LANGUAGE_FOCUS:
            return language

    deterministic_checks: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("go", (r"\bgo_compile\b", r"source_target_coverage:[^\n]*\.go\b", r"\*\*/\*\.go\b")),
        ("cpp", (r"\bcpp_compile\b", r"source_target_coverage:[^\n]*\.(?:cpp|hpp|cc|hh|cxx|hxx)\b")),
        ("rust", (r"\bcargo\b", r"\brust_compile\b", r"source_target_coverage:[^\n]*\.rs\b")),
        ("python", (r"\bpytest\b", r"source_target_coverage:[^\n]*\.py\b")),
        ("typescript", (r"\btsc\b", r"\btypescript\b", r"source_target_coverage:[^\n]*\.tsx?\b")),
        ("javascript", (r"\bjs_syntax\b", r"source_target_coverage:[^\n]*\.jsx?\b")),
        ("java", (r"\bjava_compile\b", r"source_target_coverage:[^\n]*\.java\b")),
    )
    for language, patterns in deterministic_checks:
        if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in patterns):
            return language
    return ""


def _normalize_token(value: Any, *, fallback: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    token = re.sub(r"[^a-z0-9_.:]+", "_", token).strip("_")
    return token or fallback


def _first_string(*values: Any) -> str:
    for value in values:
        token = str(value or "").strip()
        if token:
            return token
    return ""


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        token = str(value or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _dedupe_templates(templates: list[PromptProfileTemplate]) -> list[PromptProfileTemplate]:
    seen: set[str] = set()
    result: list[PromptProfileTemplate] = []
    for template in templates:
        if template.id in seen:
            continue
        seen.add(template.id)
        result.append(template)
    return result
