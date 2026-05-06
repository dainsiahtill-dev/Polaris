"""Task utilities for PM orchestration.

This module contains task processing, fallback generation, and
status tracking functions extracted from orchestration_engine.py.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from polaris.delivery.cli.pm.orchestration_core import build_enhanced_task_with_tech_stack, detect_tech_stack
from polaris.delivery.cli.pm.tasks import normalize_task_status

logger = logging.getLogger(__name__)

# ============ Constants ============

_FALLBACK_FILE_EXTENSIONS = {
    "py",
    "ts",
    "tsx",
    "js",
    "jsx",
    "go",
    "rs",
    "java",
    "kt",
    "swift",
    "json",
    "toml",
    "yaml",
    "yml",
    "md",
    "sh",
    "ps1",
}
_CODE_FILE_EXTENSIONS = {
    "py",
    "ts",
    "tsx",
    "js",
    "jsx",
    "go",
    "rs",
    "java",
    "kt",
    "swift",
}

_ROUND_HEADER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?P<header>(?:task|round|任务|轮次)\s*[A-Za-z0-9一二三四五六七八九十IVXivx]+[^\n]*)\s*:?\s*$",
    re.IGNORECASE,
)
_SECTION_BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s*(.+?)\s*$")
_DOC_STAGE_FIELD_RE = re.compile(r"^\s*([a-zA-Z_]+)\s*:\s*(.*?)\s*$")


# ============ Helper Functions ============


def _looks_like_tool_call_output(text: str) -> bool:
    """Check if output looks like a tool call."""
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    markers = (
        "[tool_call]",
        "</tool_call>",
        "<tool_call",
        "<function_call",
        "tool =>",
        '"tool":',
        "cli-mcp-server",
    )
    return any(marker in lowered for marker in markers)


def _dedupe_case_insensitive(items: list[str]) -> list[str]:
    """Remove duplicates case-insensitively."""
    merged: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = str(item or "").strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(token)
    return merged


def _normalize_candidate_file_path(value: str, *, allow_docs: bool = False) -> str:
    """Normalize a candidate file path from requirements."""
    token = str(value or "").strip().strip("`'\"")
    token = token.rstrip(".,;:)]}")
    if not token:
        return ""
    token = token.replace("\\", "/")
    if any(ch.isspace() for ch in token):
        return ""
    if "`" in token:
        return ""
    if re.fullmatch(r"[A-Za-z0-9_./-]+", token) is None:
        return ""
    while token.startswith("./"):
        token = token[2:]
    token = token.lstrip("/")
    if not token or ".." in token:
        return ""
    if token.lower().startswith(("http://", "https://")):
        return ""
    if os.path.isabs(token):
        return ""
    if "/" not in token and "." not in token:
        return ""
    if "." not in token:
        return ""
    ext = token.rsplit(".", 1)[-1].lower()
    if ext not in _FALLBACK_FILE_EXTENSIONS:
        return ""
    if not allow_docs and token.lower().startswith(("docs/", "workspace/docs/")):
        return ""
    return token


def _extract_requirement_file_candidates(
    requirements: str,
    limit: int = 18,
    *,
    allow_docs: bool = False,
) -> list[str]:
    """Extract file path candidates from requirements text."""
    text = str(requirements or "")
    if not text.strip():
        return []
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        normalized = _normalize_candidate_file_path(path, allow_docs=allow_docs)
        if not normalized:
            return
        key = normalized.lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append(normalized)

    for raw_line in text.splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        for match in re.finditer(r"`([^`]+)`", line):
            _add(match.group(1))
        bullet_token = line.lstrip("-*0123456789. \t").strip()
        if bullet_token:
            _add(bullet_token)
        if len(candidates) >= limit:
            return candidates[:limit]

    for match in re.finditer(
        r"([A-Za-z0-9_./-]+\.(?:py|tsx|ts|jsx|js|go|rs|java|kt|swift|json|toml|ya?ml|md|sh|ps1))(?![A-Za-z0-9])",
        text,
        flags=re.IGNORECASE,
    ):
        _add(match.group(1))
        if len(candidates) >= limit:
            break

    return candidates[:limit]


def _extract_pm_doc_stage_from_requirements(requirements: str) -> dict[str, Any]:
    text = str(requirements or "")
    marker = "[PM_DOC_STAGE]"
    if marker not in text:
        return {}

    after = text.split(marker, 1)[1]
    fields: dict[str, str] = {}
    for raw in after.splitlines():
        line = str(raw or "").strip()
        if not line:
            if fields:
                break
            continue
        if line.startswith("#"):
            break
        match = _DOC_STAGE_FIELD_RE.match(line)
        if match is None:
            if fields:
                break
            continue
        key = str(match.group(1) or "").strip().lower()
        value = str(match.group(2) or "").strip()
        if key and value:
            fields[key] = value

    if not fields:
        return {}

    stage_payload: dict[str, Any] = {
        "enabled": True,
        "active_stage_id": str(fields.get("active_stage_id") or "").strip(),
        "active_stage_title": str(fields.get("active_stage_title") or "").strip(),
        "active_doc_path": str(fields.get("active_document") or "").strip(),
    }
    progress = str(fields.get("stage_progress") or "").strip()
    if "/" in progress:
        left, right = progress.split("/", 1)
        try:
            stage_payload["active_stage_index"] = max(int(left.strip()) - 1, 0)
            stage_payload["total_stages"] = max(int(right.strip()), 0)
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to parse stage progress: {e}")
    return stage_payload


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (RuntimeError, ValueError):
        return default


def _resolve_docs_stage_context(
    *,
    docs_stage: dict[str, Any] | None,
    requirements: str,
) -> dict[str, Any]:
    payload = docs_stage if isinstance(docs_stage, dict) else {}
    enabled = bool(payload.get("enabled"))
    if not enabled:
        payload = _extract_pm_doc_stage_from_requirements(requirements)
        enabled = bool(payload.get("enabled"))
    if not enabled:
        return {"enabled": False}

    active_doc_path = str(payload.get("active_doc_path") or "").strip()
    if not active_doc_path:
        return {"enabled": False}
    return {
        "enabled": True,
        "active_stage_id": str(payload.get("active_stage_id") or "").strip(),
        "active_stage_title": str(payload.get("active_stage_title") or "").strip(),
        "active_doc_path": active_doc_path,
        "active_stage_index": _safe_int(payload.get("active_stage_index"), default=0),
        "total_stages": _safe_int(payload.get("total_stages"), default=0),
    }


def _annotate_tasks_with_docs_stage(tasks: list[dict[str, Any]], stage_context: dict[str, Any]) -> None:
    if not tasks or not bool(stage_context.get("enabled")):
        return
    stage_meta = {
        "active_stage_id": str(stage_context.get("active_stage_id") or "").strip(),
        "active_stage_title": str(stage_context.get("active_stage_title") or "").strip(),
        "active_doc_path": str(stage_context.get("active_doc_path") or "").strip(),
        "active_stage_index": _safe_int(stage_context.get("active_stage_index"), default=0),
        "total_stages": _safe_int(stage_context.get("total_stages"), default=0),
    }
    for task in tasks:
        if not isinstance(task, dict):
            continue
        metadata = task.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            task["metadata"] = metadata
        metadata["docs_stage"] = stage_meta


def _build_docs_stage_guard_task(
    *,
    requirements: str,
    iteration: int,
    tech_stack: dict[str, Any],
    stage_context: dict[str, Any],
    file_candidates: list[str],
) -> dict[str, Any] | None:
    if not bool(stage_context.get("enabled")):
        return None
    active_doc_path = str(stage_context.get("active_doc_path") or "").strip()
    if not active_doc_path:
        return None

    target_files = _dedupe_case_insensitive(
        [active_doc_path, *[item for item in file_candidates if item != active_doc_path][:3]]
    )
    if not target_files:
        target_files = [active_doc_path]

    scope_paths = sorted({item.rsplit("/", 1)[0] for item in target_files if "/" in item})
    stage_title = str(stage_context.get("active_stage_title") or "").strip() or "staged document"
    task = {
        "id": f"PM-{int(iteration):04d}-DS1",
        "priority": 1,
        "title": f"Docs-stage convergence: {stage_title}",
        "goal": "Execute only the active PM stage document and avoid cross-stage synthetic scaffolding.",
        "target_files": target_files,
        "scope_paths": scope_paths,
        "scope_mode": "module",
        "acceptance_criteria": [
            "Work remains constrained to files referenced by the active stage document.",
            "No synthetic bootstrap file paths are introduced outside active stage context.",
            "Task metadata records active docs-stage context for downstream auditing.",
        ],
        "assigned_to": "Director",
        "phase": "stage_gate",
    }
    enhanced = build_enhanced_task_with_tech_stack(task, tech_stack, requirements)
    return enhanced if isinstance(enhanced, dict) else None


def _ensure_fallback_quality_contract(tasks: list[dict[str, Any]], tech_stack: dict[str, Any]) -> None:
    verify_commands = _fallback_verify_commands(tech_stack)
    verify_command = verify_commands[0] if verify_commands else "python -m pytest -q"
    has_explicit_dependencies = False
    for task in tasks:
        if not isinstance(task, dict):
            continue
        dependencies = task.get("depends_on")
        if not isinstance(dependencies, list):
            dependencies = task.get("dependencies")
        if isinstance(dependencies, list) and any(str(item).strip() for item in dependencies):
            has_explicit_dependencies = True
        checklist = task.get("execution_checklist")
        if isinstance(checklist, list) and checklist:
            continue
        target_files = [str(item) for item in task.get("target_files", []) if str(item).strip()]
        target_summary = ", ".join(target_files[:4]) if target_files else "declared task scope"
        task["execution_checklist"] = [
            f"Review the task goal and current files for {target_summary}.",
            "Implement the smallest coherent change within the declared scope.",
            f"Run or document the verification command: {verify_command}",
        ]

    if len(tasks) < 2 or has_explicit_dependencies:
        return

    previous_task_id = ""
    for task in tasks:
        task_id = str(task.get("id") or "").strip()
        if previous_task_id and task_id:
            task["depends_on"] = [previous_task_id]
            task["dependencies"] = [previous_task_id]
        if task_id:
            previous_task_id = task_id


def _is_test_file(path: str) -> bool:
    lowered = str(path or "").lower()
    return "/test" in lowered or lowered.startswith("tests/")


def _is_code_file(path: str) -> bool:
    token = str(path or "").strip().lower()
    if "." not in token:
        return False
    ext = token.rsplit(".", 1)[-1]
    return ext in _CODE_FILE_EXTENSIONS


def _default_test_file_for_language(language: str) -> str:
    lang = str(language or "").strip().lower()
    if lang == "python":
        return "tests/test_service.py"
    if lang == "typescript":
        return "tests/service.test.ts"
    if lang == "javascript":
        return "tests/service.test.js"
    if lang == "go":
        return "tests/service_test.go"
    if lang == "rust":
        return "tests/integration_test.rs"
    return "tests/test_app.py"


def _fallback_verify_commands(tech_stack: dict[str, Any]) -> list[str]:
    language = str(tech_stack.get("language") or "").strip().lower()
    if language == "python":
        return ["python -m pytest -q"]
    if language == "typescript":
        return ["npm test", "npm run build"]
    if language == "javascript":
        return ["npm test", "node src/index.js"]
    if language == "go":
        return ["go test ./..."]
    if language == "rust":
        return ["cargo test"]
    return ["python -m pytest -q"]


def _synthetic_file_candidates_for_stack(tech_stack: dict[str, Any]) -> list[str]:
    """Generate deterministic bootstrap candidates when requirements have no file paths."""
    language = str(tech_stack.get("language") or "").strip().lower()
    project_type = str(tech_stack.get("project_type") or "").strip().lower()

    if language == "unknown":
        language = "typescript" if project_type in {"web"} else "python"

    if language == "typescript":
        return [
            "package.json",
            "src/main.ts",
            "src/game/server.ts",
            "src/game/session.ts",
            "tests/game.spec.ts",
        ]
    if language == "javascript":
        return [
            "package.json",
            "src/index.js",
            "src/game/server.js",
            "src/game/session.js",
            "tests/game.test.js",
        ]
    if language == "go":
        return [
            "go.mod",
            "cmd/server/main.go",
            "internal/game/session.go",
            "internal/game/matchmaking.go",
            "tests/game_test.go",
        ]
    if language == "rust":
        return [
            "Cargo.toml",
            "src/main.rs",
            "src/game/session.rs",
            "src/game/matchmaking.rs",
            "tests/game_integration.rs",
        ]
    return [
        "pyproject.toml",
        "src/fastapi_entrypoint.py",
        "src/game/session.py",
        "src/game/matchmaking.py",
        "tests/test_game_flow.py",
    ]


def _normalize_workspace_file_candidate(value: str) -> str:
    """Normalize an existing workspace file path for fallback task grounding."""
    token = str(value or "").strip().replace("\\", "/")
    while token.startswith("./"):
        token = token[2:]
    token = token.lstrip("/")
    if not token or ".." in token or token.endswith("/"):
        return ""
    lowered = token.lower()
    skip_prefixes = (
        ".git/",
        ".polaris/",
        ".venv/",
        "venv/",
        "node_modules/",
        "dist/",
        "build/",
        "coverage/",
        "__pycache__/",
    )
    if lowered.startswith(skip_prefixes):
        return ""
    if lowered.startswith(("docs/", "workspace/docs/")):
        return ""
    if "/" in lowered and any(
        part in {"node_modules", "__pycache__", ".git", ".polaris"} for part in lowered.split("/")
    ):
        return ""
    if "." not in token:
        return ""
    ext = token.rsplit(".", 1)[-1].lower()
    if ext not in _FALLBACK_FILE_EXTENSIONS:
        return ""
    return token


def _workspace_candidate_rank(path: str) -> tuple[int, str]:
    lowered = str(path or "").strip().lower()
    basename = lowered.rsplit("/", 1)[-1]
    config_names = {
        "package.json",
        "pyproject.toml",
        "tsconfig.json",
        "jest.config.ts",
        "vite.config.ts",
        "vitest.config.ts",
        "requirements.txt",
        "go.mod",
        "cargo.toml",
    }
    if basename in config_names:
        return (0, lowered)
    if lowered.startswith("src/") and _is_code_file(lowered) and not _is_test_file(lowered):
        return (1, lowered)
    if _is_code_file(lowered) and not _is_test_file(lowered):
        return (2, lowered)
    if _is_test_file(lowered):
        return (3, lowered)
    return (4, lowered)


def _select_workspace_file_candidates(workspace_files: list[str] | None, limit: int = 18) -> list[str]:
    """Select real workspace files before falling back to synthetic bootstrap paths."""
    if not isinstance(workspace_files, list):
        return []
    normalized = _dedupe_case_insensitive(
        [
            candidate
            for candidate in (_normalize_workspace_file_candidate(str(item or "")) for item in workspace_files)
            if candidate
        ]
    )
    if not normalized:
        return []
    ordered = sorted(normalized, key=_workspace_candidate_rank)
    return ordered[: max(1, int(limit or 18))]


def _extract_round_sections(requirements: str, limit: int = 6) -> list[dict[str, str]]:
    """Extract round/section headers and bodies from requirements."""
    lines = str(requirements or "").splitlines()
    sections: list[dict[str, str]] = []
    current_header = ""
    current_body: list[str] = []

    def _flush_section() -> None:
        nonlocal current_header, current_body
        if not current_header:
            return
        body_text = "\n".join(current_body).strip()
        sections.append({"header": current_header, "body": body_text})
        current_header = ""
        current_body = []

    for raw in lines:
        line = str(raw or "").strip()
        match = _ROUND_HEADER_RE.match(line)
        if match:
            _flush_section()
            current_header = str(match.group("header") or "").strip()
            current_body = []
            if len(sections) >= limit:
                break
            continue
        if current_header:
            current_body.append(raw)

    if len(sections) < limit:
        _flush_section()
    return sections[:limit]


def _extract_section_items(text: str, limit: int = 8) -> list[str]:
    """Extract bullet points from a section body."""
    items: list[str] = []
    for raw in str(text or "").splitlines():
        line = str(raw or "").strip()
        if not line:
            continue
        match = _SECTION_BULLET_RE.match(line)
        token = str(match.group(1) if match else line).strip()
        token = token.strip("`")
        if not token:
            continue
        if token not in items:
            items.append(token)
        if len(items) >= limit:
            break
    return items


def _infer_round_phase(header: str, body: str, index: int) -> str:
    """Infer the phase (bootstrap/implementation/verification/refactor) from section content."""
    text = f"{header}\n{body}".lower()
    if any(marker in text for marker in ("delete", "remove", "refactor", "cleanup", "删除", "重构", "清理")):
        return "refactor"
    if any(marker in text for marker in ("modify", "update", "enhance", "修改", "更新", "增强")):
        return "implementation"
    if any(marker in text for marker in ("test", "verify", "qa", "测试", "验证")):
        return "verification"
    if any(marker in text for marker in ("add", "create", "bootstrap", "新增", "创建")):
        return "bootstrap"
    return "bootstrap" if index <= 1 else "implementation"


# ============ Fallback Task Generation ============


def build_round_fallback_tasks(
    *,
    requirements: str,
    iteration: int,
    tech_stack: dict[str, Any],
    file_candidates: list[str],
    allow_docs: bool = False,
) -> list[dict[str, Any]]:
    """Build fallback tasks from requirements round sections."""
    sections = _extract_round_sections(requirements, limit=6)
    if len(sections) < 2:
        return []

    test_files = [item for item in file_candidates if _is_test_file(item)]
    non_test_files = [item for item in file_candidates if item not in test_files]
    primary_impl = non_test_files[0] if non_test_files else ""
    primary_test = test_files[0] if test_files else ""

    tasks: list[dict[str, Any]] = []
    for index, section in enumerate(sections, start=1):
        header = str(section.get("header") or "").strip()
        body = str(section.get("body") or "").strip()
        section_text = f"{header}\n{body}".strip()
        section_candidates = _extract_requirement_file_candidates(
            section_text,
            limit=12,
            allow_docs=allow_docs,
        )
        section_test_files = [item for item in section_candidates if _is_test_file(item)]
        section_non_test_files = [item for item in section_candidates if item not in section_test_files]

        target_files: list[str] = []
        if primary_impl:
            target_files.append(primary_impl)
        target_files.extend(section_non_test_files)

        lower_section = section_text.lower()
        wants_test = any(marker in lower_section for marker in ("test", "pytest", "qa", "verify", "测试", "验证"))
        if wants_test and primary_test:
            target_files.append(primary_test)
        elif wants_test and section_test_files:
            target_files.append(section_test_files[0])

        target_files = _dedupe_case_insensitive(target_files)
        if not target_files:
            continue

        section_items = _extract_section_items(body, limit=8)
        acceptance_criteria = section_items[:4]
        if not acceptance_criteria:
            acceptance_criteria = [f"Round {index} requirements are implemented in target files."]

        goal = acceptance_criteria[0]
        scope_paths = sorted({item.rsplit("/", 1)[0] for item in target_files if "/" in item})
        phase = _infer_round_phase(header, body, index)
        normalized_header = re.sub(r"\s+", " ", header).strip() or f"Round {index}"
        base_task = {
            "id": f"PM-{int(iteration):04d}-R{index}",
            "priority": index,
            "title": f"Requirements round {index}: {normalized_header}",
            "goal": goal,
            "target_files": target_files,
            "scope_paths": scope_paths,
            "scope_mode": "module",
            "acceptance_criteria": acceptance_criteria,
            "assigned_to": "Director",
            "phase": phase,
        }
        tasks.append(build_enhanced_task_with_tech_stack(base_task, tech_stack, requirements))

    return tasks


def build_requirements_fallback_payload(
    *,
    requirements: str,
    iteration: int,
    timestamp: str,
    plan_text: str = "",
    workspace_files: list[str] | None = None,
    docs_stage: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build fallback payload with technology stack detection.

    For unmanned automated factory operation, this function autonomously
    detects the target language and framework from requirements.
    """
    from polaris.delivery.cli.pm.tasks import normalize_pm_payload

    # Detect technology stack from requirements
    tech_stack = detect_tech_stack(requirements, plan_text, workspace_files)
    stage_context = _resolve_docs_stage_context(docs_stage=docs_stage, requirements=requirements)
    allow_docs_paths = bool(stage_context.get("enabled"))
    file_candidates = _extract_requirement_file_candidates(
        requirements,
        limit=18,
        allow_docs=allow_docs_paths,
    )
    used_synthetic_candidates = False
    used_workspace_candidates = False
    if not file_candidates and not allow_docs_paths:
        file_candidates = _select_workspace_file_candidates(workspace_files, limit=18)
        used_workspace_candidates = bool(file_candidates)
    if not file_candidates and not allow_docs_paths:
        file_candidates = _synthetic_file_candidates_for_stack(tech_stack)
        used_synthetic_candidates = bool(file_candidates)
    if not file_candidates and not allow_docs_paths:
        return None

    tasks = build_round_fallback_tasks(
        requirements=requirements,
        iteration=iteration,
        tech_stack=tech_stack,
        file_candidates=file_candidates,
        allow_docs=allow_docs_paths,
    )
    _annotate_tasks_with_docs_stage(tasks, stage_context)

    if not tasks and allow_docs_paths:
        stage_guard_task = _build_docs_stage_guard_task(
            requirements=requirements,
            iteration=iteration,
            tech_stack=tech_stack,
            stage_context=stage_context,
            file_candidates=file_candidates,
        )
        if isinstance(stage_guard_task, dict):
            tasks.append(stage_guard_task)
            _annotate_tasks_with_docs_stage(tasks, stage_context)

    if not tasks and not allow_docs_paths:
        test_files = [item for item in file_candidates if _is_test_file(item)]
        non_test_files = [item for item in file_candidates if item not in test_files]
        verify_commands = _fallback_verify_commands(tech_stack)

        code_files = [item for item in non_test_files if _is_code_file(item)]
        support_files = [item for item in non_test_files if item not in code_files]
        primary_impl = code_files[0] if code_files else (non_test_files[0] if non_test_files else "")

        bootstrap_files = _dedupe_case_insensitive((support_files[:3] + code_files[:2])[:6])
        if not bootstrap_files:
            bootstrap_files = non_test_files[:4]

        implementation_files: list[str] = []
        if primary_impl:
            implementation_files.append(primary_impl)
        for item in code_files:
            if item not in implementation_files:
                implementation_files.append(item)
        if not implementation_files:
            implementation_files = non_test_files[:4]
        implementation_files = _dedupe_case_insensitive(implementation_files[:6])

        if not test_files:
            default_test = _default_test_file_for_language(str(tech_stack.get("language") or ""))
            test_files = [default_test]

        if bootstrap_files:
            base_task = {
                "id": f"PM-{int(iteration):04d}-F1",
                "priority": 1,
                "title": "Requirements fallback bootstrap",
                "goal": "Create initial project files derived from requirements.",
                "target_files": bootstrap_files,
                "scope_paths": sorted({item.rsplit("/", 1)[0] for item in bootstrap_files if "/" in item}),
                "scope_mode": "module",
                "acceptance_criteria": [
                    "Bootstrap target files are created and syntactically valid.",
                    f"At least one verification command is runnable: {verify_commands[0]}",
                ],
                "assigned_to": "Director",
                "phase": "bootstrap",
            }
            enhanced_task = build_enhanced_task_with_tech_stack(base_task, tech_stack, requirements)
            tasks.append(enhanced_task)

        if implementation_files:
            base_task = {
                "id": f"PM-{int(iteration):04d}-F2",
                "priority": 2,
                "title": "Requirements fallback implementation",
                "goal": "Implement core module files derived from requirements.",
                "target_files": implementation_files,
                "scope_paths": sorted({item.rsplit("/", 1)[0] for item in implementation_files if "/" in item}),
                "scope_mode": "module",
                "acceptance_criteria": [
                    "Core module files are implemented with coherent behavior.",
                    "Primary implementation file contains non-trivial business logic.",
                ],
                "assigned_to": "Director",
                "phase": "implementation",
            }
            enhanced_task = build_enhanced_task_with_tech_stack(base_task, tech_stack, requirements)
            tasks.append(enhanced_task)

        if test_files:
            base_task = {
                "id": f"PM-{int(iteration):04d}-F3",
                "priority": 3,
                "title": "Requirements fallback tests",
                "goal": "Create or update tests derived from requirements.",
                "target_files": test_files[:6],
                "scope_paths": sorted({item.rsplit("/", 1)[0] for item in test_files if "/" in item}),
                "scope_mode": "module",
                "acceptance_criteria": [
                    "At least one test file exists and validates core behavior.",
                    f"Verification command passes: {verify_commands[0]}",
                ],
                "assigned_to": "Director",
                "phase": "verification",
            }
            enhanced_task = build_enhanced_task_with_tech_stack(base_task, tech_stack, requirements)
            tasks.append(enhanced_task)
        _annotate_tasks_with_docs_stage(tasks, stage_context)

    if not tasks:
        return None
    _ensure_fallback_quality_contract(tasks, tech_stack)

    # Build enhanced overall goal with tech stack info
    lang = tech_stack.get("language", "unknown")
    framework = tech_stack.get("framework")
    project_type = tech_stack.get("project_type", "generic")

    if lang != "unknown":
        if framework:
            overall_goal = f"Build {lang.title()} {framework.title()} {project_type} from requirements."
        else:
            overall_goal = f"Build {lang.title()} {project_type} from requirements."
    else:
        overall_goal = "Fallback PM contract generated from requirements file paths."

    payload = {
        "overall_goal": overall_goal,
        "focus": "Recover from PM invalid output and continue delivery safely.",
        "tasks": tasks,
        "notes": (
            "Auto-generated fallback tasks because PM returned empty/invalid task list."
            + (
                f" Docs stage strict mode active for {str(stage_context.get('active_doc_path') or '').strip()}."
                if allow_docs_paths
                else ""
            )
            + (
                " Existing workspace files were used to ground fallback task scope."
                if used_workspace_candidates
                else ""
            )
            + (
                " Synthetic bootstrap paths were used (no file paths in requirements)."
                if used_synthetic_candidates
                else ""
            )
        ),
        "detected_tech_stack": tech_stack,
        "docs_stage": stage_context if allow_docs_paths else {},
        "quality_gate": {
            "score": 85,
            "critical_issue_count": 0,
            "summary": "Deterministic fallback contract satisfies minimal PM task quality gates.",
        },
    }
    normalized = normalize_pm_payload(payload, int(iteration or 1), str(timestamp or ""))
    if not isinstance(normalized, dict):
        return None
    normalized["quality_gate"] = payload["quality_gate"]
    return normalized


# ============ Task Status Utilities ============


def count_active_director_tasks(tasks: Any) -> int:
    """Count active (non-completed) Director tasks."""
    if not isinstance(tasks, list):
        return 0
    count = 0
    for item in tasks:
        if not isinstance(item, dict):
            continue
        assignee = str(item.get("assigned_to") or "").strip().lower()
        if assignee != "director":
            continue
        status = normalize_task_status(item.get("status"))
        if status in {"done", "failed", "blocked"}:
            continue
        count += 1
    return count


def get_task_signature(tasks: Any) -> str:
    """Get signature from first task for spin detection."""
    if not isinstance(tasks, list) or not tasks:
        return ""
    primary = tasks[0] if isinstance(tasks[0], dict) else {}
    return str(primary.get("fingerprint") or primary.get("id") or "").strip()


def get_active_director_tasks(tasks: Any) -> list[dict[str, Any]]:
    """Get list of active Director tasks."""
    selected: list[dict[str, Any]] = []
    if not isinstance(tasks, list):
        return selected
    for item in tasks:
        if not isinstance(item, dict):
            continue
        assignee = str(item.get("assigned_to") or "").strip().lower()
        if assignee != "director":
            continue
        status = normalize_task_status(item.get("status"))
        if status in {"done", "failed", "blocked"}:
            continue
        selected.append(item)
    return selected


def get_director_task_status_summary(tasks: Any) -> dict[str, int]:
    """Get summary of Director task statuses."""
    summary = {
        "total": 0,
        "todo": 0,
        "in_progress": 0,
        "review": 0,
        "needs_continue": 0,
        "done": 0,
        "failed": 0,
        "blocked": 0,
    }
    if not isinstance(tasks, list):
        return summary

    for item in tasks:
        if not isinstance(item, dict):
            continue
        assignee = str(item.get("assigned_to") or "").strip().lower()
        if assignee != "director":
            continue
        summary["total"] += 1
        status = normalize_task_status(item.get("status"))
        if status in summary:
            summary[status] += 1
    return summary


def to_bool(value: Any, default: bool) -> bool:
    """Convert value to boolean with default."""
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


__all__ = [
    "build_requirements_fallback_payload",
    "build_round_fallback_tasks",
    "count_active_director_tasks",
    "get_active_director_tasks",
    "get_director_task_status_summary",
    "get_task_signature",
    "to_bool",
]
