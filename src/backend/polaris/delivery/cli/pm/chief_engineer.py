"""Deterministic ChiefEngineer blueprint service for PM -> Director bridge.

ChiefEngineer (工部尚书) - Blueprint Designer
=============================================
Role: Designs construction blueprints (module/file/method level) before implementation.
Relationship with Director: ChiefEngineer designs the "图纸" (blueprint), Director follows it.

Key Responsibilities:
1. Analyze task requirements and dependencies from pm_tasks.json
2. Design module/file/method level implementation plan
3. Generate TaskBlueprint with:
   - target_files: Files to be modified/created
   - scope_paths: Scope of the implementation
   - unresolved_imports: Dependencies to resolve
   - scope_for_apply: Valid range for code changes
4. Provide technical guidance to Director

IMPORTANT DISTINCTION:
- ChiefEngineer (工部尚书): Designs blueprints, does NOT write implementation code
- Director (工部侍郎): Follows blueprints, writes actual implementation
- They are SEPARATE roles working in sequence, not merged.

Usage Flow:
    PM Loop -> ChiefEngineer (designs blueprint) -> Director (implements) -> QA/Auditor
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, cast

from polaris.delivery.cli.pm.utils import normalize_path_list
from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.storage.io_paths import resolve_artifact_path
from polaris.kernelone.runtime.shared_types import normalize_path
from polaris.kernelone.utils.time_utils import utc_now_str

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Sequence

# 代码智能服务集成 (可选依赖)
try:
    from polaris.infrastructure.code_intelligence import (
        CodeIntelligenceService,
        IncrementalSemanticAnalyzer,
    )

    CODE_INTEL_AVAILABLE = True
except (RuntimeError, ValueError):
    CODE_INTEL_AVAILABLE = False


_TERMINAL_TASK_STATUSES = {"done", "failed", "blocked"}
_JS_TS_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
_CODE_EXTENSIONS = _JS_TS_EXTENSIONS | {".py", ".go", ".rs"}
_METHOD_TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
_METHOD_STOPWORDS = {
    "the",
    "and",
    "with",
    "from",
    "that",
    "this",
    "then",
    "when",
    "must",
    "should",
    "into",
    "task",
    "file",
    "files",
    "module",
    "modules",
    "build",
    "test",
    "tests",
    "code",
    "class",
    "function",
    "method",
    "system",
    "project",
    "data",
    "result",
    "results",
    "status",
    "director",
    "chief",
    "engineer",
    "implement",
    "implementation",
}

_RE_JS_TS_IMPORT = re.compile(
    r"(?:from\s+['\"](?P<from>\.{1,2}/[^'\"]+)['\"])|"
    r"(?:require\(\s*['\"](?P<req>\.{1,2}/[^'\"]+)['\"]\s*\))|"
    r"(?:import\s+['\"](?P<side>\.{1,2}/[^'\"]+)['\"])",
    re.MULTILINE,
)
_RE_PY_RELATIVE_IMPORT = re.compile(r"^\s*from\s+(\.{1,2}[a-zA-Z0-9_\.]*)\s+import\s+", re.MULTILINE)
_RE_GO_IMPORT_BLOCK = re.compile(r"import\s*\((?P<body>.*?)\)", re.DOTALL)
_RE_GO_IMPORT_SINGLE = re.compile(r"import\s+\"(?P<path>[^\"]+)\"")
_RE_GO_IMPORT_INNER = re.compile(r"\"(?P<path>[^\"]+)\"")
_RE_RUST_USE = re.compile(r"^\s*use\s+([a-zA-Z0-9_:]+)\s*;?", re.MULTILINE)

_RE_SYMBOLS_BY_LANGUAGE = {
    "python": [
        re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.MULTILINE),
        re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.MULTILINE),
    ],
    "typescript": [
        re.compile(
            r"^\s*export\s+(?:class|interface|type|enum|const|function)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            re.MULTILINE,
        ),
        re.compile(
            r"^\s*(?:class|interface|type|enum|const|function)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            re.MULTILINE,
        ),
    ],
    "javascript": [
        re.compile(
            r"^\s*export\s+(?:class|const|function)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            re.MULTILINE,
        ),
        re.compile(r"^\s*(?:class|const|function)\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.MULTILINE),
    ],
    "go": [
        re.compile(r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.MULTILINE),
        re.compile(r"^\s*func\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.MULTILINE),
    ],
    "rust": [
        re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.MULTILINE),
        re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.MULTILINE),
    ],
}


@dataclass
class BlueprintFile:
    path: str
    exists: bool
    language: str
    module: str
    imports: list[str] = field(default_factory=list)
    resolved_imports: list[str] = field(default_factory=list)
    unresolved_imports: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    checksum: str = ""
    updated_at: str = ""


@dataclass
class TaskBlueprint:
    task_id: str
    title: str
    goal: str
    target_files: list[str] = field(default_factory=list)
    scope_paths: list[str] = field(default_factory=list)
    missing_targets: list[str] = field(default_factory=list)
    unresolved_imports: list[str] = field(default_factory=list)
    scope_for_apply: list[str] = field(default_factory=list)
    verify_ready: bool = False
    continue_reason: str = ""
    updated_at: str = ""
    # 代码智能增强字段
    context_pack: dict[str, Any] = field(default_factory=dict)
    semantic_files: list[str] = field(default_factory=list)
    # 增量分析字段
    incremental_analysis: dict[str, Any] = field(default_factory=dict)
    affected_files: list[str] = field(default_factory=list)
    risk_level: str = "unknown"


@dataclass
class ApiContract:
    """API contract between modules."""

    provider: str = ""  # 提供接口的模块
    consumer: str = ""  # 消费接口的模块
    interface_name: str = ""  # 接口名称
    methods: list[str] = field(default_factory=list)  # 方法签名列表
    data_types: list[str] = field(default_factory=list)  # 数据类型定义


@dataclass
class DataFlowEdge:
    """Data flow between modules."""

    source: str = ""  # 数据源模块
    target: str = ""  # 数据目标模块
    data_type: str = ""  # 数据类型
    flow_type: str = ""  # 流向: import, call, event


@dataclass
class ModuleArchitecture:
    """Architecture metadata for a module."""

    module_id: str = ""
    layer: int = 0  # 架构层级: 0=基础设施, 1=核心领域, 2=应用服务, 3=接口层
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)  # 反向依赖（哪些模块依赖我）
    exposed_interfaces: list[str] = field(default_factory=list)  # 对外暴露的接口
    internal_impl: list[str] = field(default_factory=list)  # 内部实现细节
    stability_score: float = 0.0  # 稳定性评分: 被依赖越多越稳定


@dataclass
class ArchitectureDecision:
    """架构决策记录 (ADR)."""

    decision_id: str = ""
    title: str = ""
    context: str = ""
    decision: str = ""
    consequences: list[str] = field(default_factory=list)
    status: str = "proposed"  # proposed, accepted, deprecated, superseded
    created_at: str = ""
    related_modules: list[str] = field(default_factory=list)


@dataclass
class ModuleRestructuring:
    """模块重组建议."""

    action: str = ""  # create, merge, split, move, deprecate
    target_module: str = ""
    reason: str = ""
    source_modules: list[str] = field(default_factory=list)  # 对于 merge/split
    new_location: str = ""  # 对于 move
    impact_tasks: list[str] = field(default_factory=list)  # 影响哪些任务


@dataclass
class ProjectBlueprint:
    schema_version: int = 1
    owner: str = "ChiefEngineer"
    workspace: str = ""
    run_id: str = ""
    pm_iteration: int = 0
    updated_at: str = ""
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    modules: dict[str, dict[str, Any]] = field(default_factory=dict)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    # 新增: 全局架构上下文
    module_order: list[str] = field(default_factory=list)  # 模块拓扑排序（构建顺序）
    api_contracts: list[dict[str, Any]] = field(default_factory=list)  # 模块间API契约
    data_flows: list[dict[str, Any]] = field(default_factory=list)  # 数据流图
    module_architecture: dict[str, dict[str, Any]] = field(default_factory=dict)  # 模块架构元数据
    architecture_constraints: list[str] = field(default_factory=list)  # 架构约束规则
    # 新增: 架构演进规划
    planned_modules: list[dict[str, Any]] = field(default_factory=list)  # 计划新增模块
    deprecated_modules: list[str] = field(default_factory=list)  # 计划废弃模块
    architecture_decisions: list[dict[str, Any]] = field(default_factory=list)  # 架构决策记录
    module_restructuring: list[dict[str, Any]] = field(default_factory=list)  # 模块重组建议
    evolution_roadmap: list[dict[str, Any]] = field(default_factory=list)  # 架构演进路线图


# Backward compatibility alias
_utc_now_iso = utc_now_str


def _dedupe(items: Sequence[str]) -> list[str]:
    merged: list[str] = []
    for item in items:
        token = str(item or "").strip()
        if not token or token in merged:
            continue
        merged.append(token)
    return merged


def _is_directory_like(path: str) -> bool:
    normalized = normalize_path(str(path or "").strip()).replace("\\", "/")
    if not normalized:
        return False
    if normalized.endswith("/"):
        return True
    leaf = os.path.basename(normalized)
    return "." not in leaf


def _is_valid_target_file(path: str) -> bool:
    """Check if path looks like a valid target file path.

    Filters out:
    - Long descriptive text (e.g., "创建项目目录结构配置pyproject.toml并安装依赖")
    - Requirement descriptions that are clearly not paths (e.g., "Persist data to local JSON file (~/.todo-list/data.json")
    - Acceptance criteria notes with brackets (e.g., "All CRUD functions have corresponding Jest unit tests [API: src/todo.service.test.ts")
    """
    normalized = str(path or "").strip()
    if not normalized:
        return False

    # Reject if way too long (likely a description sentence)
    if len(normalized) > 100:
        return False

    # Check if it has a valid file extension
    has_file_extension = any(
        normalized.lower().endswith(ext)
        for ext in [
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",
            ".py",
            ".go",
            ".rs",
            ".java",
            ".kt",
            ".swift",
            ".json",
            ".toml",
            ".yaml",
            ".yml",
            ".md",
            ".css",
            ".scss",
            ".less",
            ".html",
            ".htm",
            ".sql",
            ".sh",
            ".bat",
            ".ps1",
            ".mod",
            ".sum",
        ]
    )

    # Check for config files without extension
    is_config_file = os.path.basename(normalized.lower()) in [
        "dockerfile",
        "makefile",
        "readme",
        "license",
        "go.mod",
        "go.sum",
        ".gitignore",
        ".dockerignore",
        ".env",
        ".env.example",
        "cargo.toml",
    ]

    # If it has a valid extension or is a known config file, accept it
    # even if it has some Chinese characters (like "见 `docs/product/adr.md")
    if has_file_extension or is_config_file:
        return True

    # For paths without extension, be more strict
    # Reject if contains Chinese characters and no clear file indicator
    if any("\u4e00" <= char <= "\u9fff" for char in normalized):
        return False

    # Reject common description patterns (multi-word descriptions without file extensions)
    description_indicators = [
        "persist",
        "create",
        "build",
        "implement",
        "configure",
        "setup",
        "install",
        "verify",
        "corresponding",
        "coverage",
    ]
    word_count = len(normalized.split())
    lower = normalized.lower()

    # If it has multiple words and contains description indicators, reject
    return not (word_count >= 4 and any(ind in lower for ind in description_indicators))

    # Accept short simple paths (likely directory or simple file references)
    return True


def _language_from_path(path: str) -> str:
    lowered = str(path or "").strip().lower()
    if lowered.endswith((".ts", ".tsx")):
        return "typescript"
    if lowered.endswith((".js", ".jsx", ".mjs", ".cjs")):
        return "javascript"
    if lowered.endswith(".py"):
        return "python"
    if lowered.endswith(".go"):
        return "go"
    if lowered.endswith(".rs"):
        return "rust"
    return "unknown"


def _module_key(path: str) -> str:
    normalized = normalize_path(str(path or "").strip()).replace("\\", "/")
    if not normalized:
        return "root"
    parts = [p for p in normalized.split("/") if p]
    if not parts:
        return "root"
    if parts[0] in {"src", "app", "backend", "frontend", "cmd", "internal", "lib"} and len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (RuntimeError, ValueError):
        return ""


def _sha256(content: str) -> str:
    return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()


def _candidate_relative_module_paths(
    workspace_full: str,
    source_file: str,
    module_ref: str,
) -> list[str]:
    source_dir = os.path.dirname(source_file)
    source_ext = os.path.splitext(source_file)[1].lower()
    if module_ref.startswith("."):
        dot_count = len(module_ref) - len(module_ref.lstrip("."))
        tail = module_ref.lstrip(".").replace(".", "/")
        if dot_count <= 1:
            module_ref = f"./{tail}" if tail else "."
        else:
            parent = "../" * (dot_count - 1)
            module_ref = f"{parent}{tail}" if tail else parent.rstrip("/")

    source_abs = os.path.join(workspace_full, source_dir)
    base_abs = os.path.normpath(os.path.join(source_abs, module_ref))
    base_rel = normalize_path(os.path.relpath(base_abs, workspace_full))

    candidates = [base_rel]
    candidates.extend(
        [
            base_rel + ".ts",
            base_rel + ".tsx",
            base_rel + ".js",
            base_rel + ".jsx",
            base_rel + ".mjs",
            base_rel + ".cjs",
            base_rel + ".py",
            base_rel + ".go",
            base_rel + ".rs",
            normalize_path(os.path.join(base_rel, "index.ts")),
            normalize_path(os.path.join(base_rel, "index.tsx")),
            normalize_path(os.path.join(base_rel, "index.js")),
            normalize_path(os.path.join(base_rel, "index.jsx")),
            normalize_path(os.path.join(base_rel, "__init__.py")),
        ]
    )
    if source_ext:
        candidates.insert(1, base_rel + source_ext)
    return _dedupe([normalize_path(path) for path in candidates if normalize_path(path)])


def _resolve_relative_import(
    workspace_full: str,
    source_file: str,
    module_ref: str,
) -> tuple[str, str]:
    candidates = _candidate_relative_module_paths(workspace_full, source_file, module_ref)
    for rel in candidates:
        full = os.path.join(workspace_full, rel)
        if os.path.isfile(full):
            return rel, ""
    hint = candidates[0] if candidates else ""
    return "", hint


def _extract_imports(language: str, content: str) -> list[str]:
    imports: list[str] = []
    if language in {"typescript", "javascript"}:
        for match in _RE_JS_TS_IMPORT.finditer(content):
            token = str(match.group("from") or match.group("req") or match.group("side") or "").strip()
            if token:
                imports.append(token)
    elif language == "python":
        for match in _RE_PY_RELATIVE_IMPORT.finditer(content):
            token = str(match.group(1) or "").strip()
            if token:
                imports.append(token)
    elif language == "go":
        for block in _RE_GO_IMPORT_BLOCK.finditer(content):
            body = str(block.group("body") or "")
            for match in _RE_GO_IMPORT_INNER.finditer(body):
                token = str(match.group("path") or "").strip()
                if token:
                    imports.append(token)
        for match in _RE_GO_IMPORT_SINGLE.finditer(content):
            token = str(match.group("path") or "").strip()
            if token:
                imports.append(token)
    elif language == "rust":
        for match in _RE_RUST_USE.finditer(content):
            token = str(match.group(1) or "").strip()
            if token:
                imports.append(token)
    return _dedupe(imports)


def _extract_symbols(language: str, content: str) -> list[str]:
    symbols: list[str] = []
    patterns = _RE_SYMBOLS_BY_LANGUAGE.get(language, [])
    for pattern in patterns:
        for match in pattern.finditer(content):
            token = str(match.group(1) or "").strip()
            if token and token not in symbols:
                symbols.append(token)
            if len(symbols) >= 64:
                return symbols
    return symbols


def _extract_method_candidates(texts: Sequence[str]) -> list[str]:
    candidates: list[str] = []
    for text in texts:
        source = str(text or "").strip()
        if not source:
            continue
        for token in _METHOD_TOKEN_RE.findall(source):
            lowered = token.lower()
            if lowered in _METHOD_STOPWORDS:
                continue
            if token.isupper():
                continue
            # keep likely method/symbol tokens and compact identifiers
            if "_" in token or (token[0].islower() and any(ch.isupper() for ch in token[1:])):
                if token not in candidates:
                    candidates.append(token)
                continue
            if lowered.endswith(("manager", "service", "controller", "handler", "client", "repo")):
                if token not in candidates:
                    candidates.append(token)
                continue
    return candidates[:32]


def _classify_file_role(path: str) -> str:
    normalized = normalize_path(str(path or "").strip()).lower()
    base = os.path.basename(normalized)
    if (
        "/tests/" in normalized
        or normalized.startswith("tests/")
        or base.startswith("test_")
        or base.endswith("_test.py")
    ):
        return "test"
    if base in {"fastapi_entrypoint.py", "main.ts", "main.js", "index.ts", "index.js", "app.py", "app.ts", "app.js"}:
        return "entrypoint"
    if base.endswith((".md", ".txt", ".json", ".yaml", ".yml")):
        return "config_or_docs"
    return "implementation"


def _build_file_construction_plan(
    *,
    path: str,
    task: dict[str, Any],
    task_blueprint: dict[str, Any],
    file_blueprint: dict[str, Any] | None,
) -> dict[str, Any]:
    acceptance = task.get("acceptance_criteria") or task.get("acceptance") or []
    acceptance_text = [str(item).strip() for item in acceptance if str(item).strip()]
    goal = str(task.get("goal") or "").strip()
    title = str(task.get("title") or "").strip()

    resolved_symbols = file_blueprint.get("symbols") if isinstance(file_blueprint, dict) else []
    known_symbols = [str(item).strip() for item in (resolved_symbols or []) if str(item).strip()]
    unresolved_tokens = [
        str(item).split(":", 1)[-1].strip()
        for item in (task_blueprint.get("unresolved_imports") or [])
        if ":" in str(item)
    ]
    method_hints = _extract_method_candidates([title, goal, *acceptance_text, *unresolved_tokens])
    method_names = _dedupe(known_symbols + method_hints)[:12]

    steps: list[str] = [
        f"Create/update `{path}` and keep scope inside approved paths.",
        "Implement minimal compile-ready structure before adding advanced behaviors.",
    ]
    if method_names:
        steps.append("Implement symbols/methods in this file: " + ", ".join(method_names) + ".")
    if acceptance_text:
        steps.append("Map code behaviors to acceptance items for this file.")
    steps.append("Add/update tests or validation hooks that prove this file's responsibilities.")

    return {
        "path": path,
        "role": _classify_file_role(path),
        "method_names": method_names,
        "implementation_steps": steps,
        "acceptance_bindings": acceptance_text[:8],
    }


def _build_task_construction_plan(
    *,
    task: dict[str, Any],
    task_blueprint: dict[str, Any],
    file_payload_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    task_id = str(task.get("id") or "").strip()
    title = str(task.get("title") or "").strip()
    goal = str(task.get("goal") or "").strip()
    target_files = [path for path in normalize_path_list(task.get("target_files") or []) if _is_valid_target_file(path)]
    scope_for_apply = normalize_path_list(task_blueprint.get("scope_for_apply") or [])
    plan_files = _dedupe(target_files + scope_for_apply)

    file_plans = [
        _build_file_construction_plan(
            path=path,
            task=task,
            task_blueprint=task_blueprint,
            file_blueprint=file_payload_map.get(path) if isinstance(file_payload_map.get(path), dict) else None,
        )
        for path in plan_files
    ]

    method_catalog = _dedupe([symbol for file_plan in file_plans for symbol in (file_plan.get("method_names") or [])])
    coordinator_prompts = [
        "If blocked by missing files/dependencies, ask ChiefEngineer for an updated scope_for_apply union and continue same task.",
        "If compile fails due to unresolved symbols, report the exact symbol+file pair to ChiefEngineer and request symbol-level patch steps.",
        "Do not switch to new tasks until current task reaches verify_ready or build budget is exhausted.",
    ]
    qa_checklist = [
        "All missing target files are created or intentionally waived with evidence.",
        "Unresolved imports trend is non-increasing across build rounds.",
        "Method-level responsibilities map to acceptance criteria.",
    ]

    return {
        "schema_version": 1,
        "task_id": task_id,
        "title": title,
        "goal": goal,
        "verify_ready": bool(task_blueprint.get("verify_ready")),
        "scope_for_apply": scope_for_apply,
        "method_catalog": method_catalog[:32],
        "file_plans": file_plans[:32],
        "qa_checklist": qa_checklist,
        "coordination_prompts": coordinator_prompts,
    }


def _expand_scope_files(
    workspace_full: str,
    scope_paths: Sequence[str],
    *,
    limit: int = 24,
) -> list[str]:
    selected: list[str] = []
    for scope in normalize_path_list(scope_paths):
        full = os.path.join(workspace_full, scope)
        if os.path.isfile(full):
            ext = os.path.splitext(full)[1].lower()
            if ext in _CODE_EXTENSIONS and scope not in selected:
                selected.append(scope)
            continue
        if not os.path.isdir(full):
            continue
        for root, dirs, files in os.walk(full):
            dirs[:] = [
                d
                for d in dirs
                if d not in {".git", "node_modules", "__pycache__", ".venv", "venv", ".polaris", ".polaris"}
            ]
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext not in _CODE_EXTENSIONS:
                    continue
                rel = normalize_path(os.path.relpath(os.path.join(root, name), workspace_full))
                if rel and rel not in selected:
                    selected.append(rel)
                if len(selected) >= limit:
                    return selected
    return selected


def _analyze_file(
    workspace_full: str,
    rel_path: str,
) -> tuple[BlueprintFile, list[str]]:
    normalized = normalize_path(str(rel_path or "").strip())
    full = os.path.join(workspace_full, normalized)
    exists = os.path.isfile(full)
    language = _language_from_path(normalized)
    content = _read_text(full) if exists else ""
    imports = _extract_imports(language, content)
    symbols = _extract_symbols(language, content)
    resolved_imports: list[str] = []
    unresolved_imports: list[str] = []
    missing_dependency_hints: list[str] = []

    for token in imports:
        if language in {"typescript", "javascript", "python"} and (
            token.startswith("./") or token.startswith("../") or token.startswith(".")
        ):
            resolved_rel, hint = _resolve_relative_import(workspace_full, normalized, token)
            if resolved_rel:
                resolved_imports.append(resolved_rel)
            else:
                unresolved_imports.append(f"{normalized}:{token}")
                if hint:
                    missing_dependency_hints.append(hint)

    payload = BlueprintFile(
        path=normalized,
        exists=exists,
        language=language,
        module=_module_key(normalized),
        imports=imports,
        resolved_imports=_dedupe(resolved_imports),
        unresolved_imports=_dedupe(unresolved_imports),
        symbols=symbols,
        checksum=_sha256(content) if exists else "",
        updated_at=_utc_now_iso(),
    )
    return payload, _dedupe(missing_dependency_hints)


def _active_director_tasks(tasks: Any) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    if not isinstance(tasks, list):
        return selected
    for item in tasks:
        if not isinstance(item, dict):
            continue
        assignee = str(item.get("assigned_to") or "").strip().lower()
        if assignee != "director":
            continue
        status = str(item.get("status") or "todo").strip().lower()
        if status in _TERMINAL_TASK_STATUSES:
            continue
        selected.append(item)
    return selected


def _build_modules_from_files(files_payload: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    modules: dict[str, dict[str, Any]] = {}

    for rel_path, file_payload in files_payload.items():
        if not isinstance(file_payload, dict):
            continue
        module = str(file_payload.get("module") or _module_key(rel_path)).strip() or "root"
        mod = modules.setdefault(
            module,
            {
                "files": [],
                "dependencies": [],
                "unresolved_dependencies": [],
            },
        )
        if rel_path not in mod["files"]:
            mod["files"].append(rel_path)

        for dep_rel in normalize_path_list(file_payload.get("resolved_imports") or []):
            dep_module = _module_key(dep_rel)
            if dep_module != module and dep_module not in mod["dependencies"]:
                mod["dependencies"].append(dep_module)

        unresolved = file_payload.get("unresolved_imports") or []
        if isinstance(unresolved, list):
            for token in unresolved:
                ref = str(token or "").strip()
                if not ref:
                    continue
                unresolved_module = _module_key(ref.split(":", 1)[-1].strip())
                if unresolved_module and unresolved_module not in mod["unresolved_dependencies"]:
                    mod["unresolved_dependencies"].append(unresolved_module)

    for module_payload in modules.values():
        module_payload["files"] = _dedupe(module_payload.get("files") or [])
        module_payload["dependencies"] = _dedupe(module_payload.get("dependencies") or [])
        module_payload["unresolved_dependencies"] = _dedupe(module_payload.get("unresolved_dependencies") or [])
    return modules


def _topological_sort_modules(modules: dict[str, dict[str, Any]]) -> list[str]:
    """对模块进行拓扑排序，确定构建顺序（底层优先）。"""
    # 计算入度（被依赖的数量）
    in_degree: dict[str, int] = dict.fromkeys(modules, 0)
    dependents: dict[str, list[str]] = {mod: [] for mod in modules}  # 反向图

    for mod, payload in modules.items():
        for dep in payload.get("dependencies", []):
            if dep in modules and dep != mod:
                in_degree[mod] = in_degree.get(mod, 0) + 1
                dependents[dep].append(mod)

    # 从入度为0的模块开始（最底层，不依赖其他模块）
    queue = [mod for mod, degree in in_degree.items() if degree == 0]
    result = []

    while queue:
        # 按名称排序以保持确定性
        queue.sort()
        current = queue.pop(0)
        result.append(current)

        for dependent in dependents.get(current, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # 如果有环，将剩余模块按名称添加
    remaining = [mod for mod in modules if mod not in result]
    result.extend(sorted(remaining))

    return result


def _calculate_module_layers(modules: dict[str, dict[str, Any]], module_order: list[str]) -> dict[str, int]:
    """计算每个模块的架构层级（0=最底层基础设施）。"""
    layers: dict[str, int] = {}

    # 按拓扑顺序计算层级
    for mod in module_order:
        payload = modules.get(mod, {})
        deps = payload.get("dependencies", [])

        if not deps:
            layers[mod] = 0
        else:
            # 层级 = max(依赖模块层级) + 1
            max_dep_layer = 0
            for dep in deps:
                if dep in layers:
                    max_dep_layer = max(max_dep_layer, layers[dep])
            layers[mod] = max_dep_layer + 1

    return layers


def _build_dependents_map(modules: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """构建反向依赖图（哪些模块依赖我）。"""
    dependents: dict[str, list[str]] = {mod: [] for mod in modules}

    for mod, payload in modules.items():
        for dep in payload.get("dependencies", []):
            if dep in modules and dep != mod and mod not in dependents[dep]:
                dependents[dep].append(mod)

    return dependents


def _detect_api_contracts(
    modules: dict[str, dict[str, Any]], files_payload: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """检测模块间的API契约（通过导出符号分析）。"""
    contracts: list[dict[str, Any]] = []

    # 收集每个模块的导出符号
    module_exports: dict[str, list[str]] = {mod: [] for mod in modules}
    for rel_path, file_payload in files_payload.items():
        if not isinstance(file_payload, dict):
            continue
        module = str(file_payload.get("module") or _module_key(rel_path)).strip() or "root"
        symbols = file_payload.get("symbols", [])
        if isinstance(symbols, list):
            for sym in symbols:
                if sym and sym not in module_exports.get(module, []):
                    module_exports.setdefault(module, []).append(sym)

    # 检测跨模块引用形成契约
    for rel_path, file_payload in files_payload.items():
        if not isinstance(file_payload, dict):
            continue
        module = str(file_payload.get("module") or _module_key(rel_path)).strip() or "root"
        imports = file_payload.get("resolved_imports", [])

        for imp_path in imports:
            dep_module = _module_key(imp_path)
            if dep_module != module and dep_module in modules:
                # 这是一个潜在的API契约
                imp_symbols = files_payload.get(imp_path, {}).get("symbols", [])
                contracts.append(
                    {
                        "provider": dep_module,
                        "consumer": module,
                        "interface_file": imp_path,
                        "symbols_used": imp_symbols[:8] if isinstance(imp_symbols, list) else [],
                    }
                )

    # 去重
    seen = set()
    unique_contracts = []
    for c in contracts:
        key = f"{c['provider']}->{c['consumer']}:{c['interface_file']}"
        if key not in seen:
            seen.add(key)
            unique_contracts.append(c)

    return unique_contracts[:50]  # 限制数量


def _build_data_flows(
    modules: dict[str, dict[str, Any]], files_payload: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """构建模块间数据流图。"""
    flows: list[dict[str, Any]] = []

    for rel_path, file_payload in files_payload.items():
        if not isinstance(file_payload, dict):
            continue
        source_module = str(file_payload.get("module") or _module_key(rel_path)).strip() or "root"
        imports = file_payload.get("imports", [])

        for imp in imports:
            if isinstance(imp, str) and (imp.startswith("./") or imp.startswith("../")):
                # 解析相对导入目标模块
                target_module = _module_key(imp)
                if target_module != source_module and target_module in modules:
                    flows.append(
                        {
                            "source": source_module,
                            "target": target_module,
                            "via_file": rel_path,
                            "flow_type": "import",
                        }
                    )

    # 去重
    seen = set()
    unique_flows = []
    for f in flows:
        key = f"{f['source']}->{f['target']}:{f['via_file']}"
        if key not in seen:
            seen.add(key)
            unique_flows.append(f)

    return unique_flows[:50]


def _build_module_architecture(
    modules: dict[str, dict[str, Any]],
    files_payload: dict[str, dict[str, Any]],
    module_order: list[str],
) -> dict[str, dict[str, Any]]:
    """构建完整的模块架构元数据。"""
    layers = _calculate_module_layers(modules, module_order)
    dependents = _build_dependents_map(modules)

    arch: dict[str, dict[str, Any]] = {}
    for mod, payload in modules.items():
        layer = layers.get(mod, 0)
        dep_count = len(dependents.get(mod, []))
        total_modules = len(modules)

        # 稳定性 = 被依赖数 / 总模块数（0-1之间）
        stability = dep_count / total_modules if total_modules > 0 else 0

        # 识别暴露的接口（被其他模块引用的符号）
        exposed = []
        for dep_mod in dependents.get(mod, []):
            # 检查依赖模块引用了哪些本模块的符号
            for rel_path, file_payload in files_payload.items():
                file_mod = str(file_payload.get("module") or _module_key(rel_path)).strip() or "root"
                if file_mod == dep_mod:
                    imports = file_payload.get("imports", [])
                    for imp in imports:
                        if isinstance(imp, str) and mod in imp:
                            exposed.append(f"{dep_mod} uses {imp}")

        arch[mod] = {
            "layer": layer,
            "dependencies": payload.get("dependencies", []),
            "dependents": dependents.get(mod, []),
            "stability_score": round(stability, 2),
            "exposed_interfaces": _dedupe(exposed)[:10],
            "internal_files": payload.get("files", [])[:10],
            "unresolved_deps": payload.get("unresolved_dependencies", []),
        }

    return arch


def _build_architecture_constraints(
    module_arch: dict[str, dict[str, Any]], modules: dict[str, dict[str, Any]]
) -> list[str]:
    """生成架构约束规则。"""
    constraints = []

    # 层级约束
    max_layer = max((a.get("layer", 0) for a in module_arch.values()), default=0)
    constraints.append(f"架构层级: 共{max_layer + 1}层 (0={max_layer}=接口层)")

    # 依赖方向约束
    for mod, arch in module_arch.items():
        layer = arch.get("layer", 0)
        for dep in arch.get("dependencies", []):
            dep_arch = module_arch.get(dep, {})
            dep_layer = dep_arch.get("layer", 0)
            if dep_layer > layer:
                # 上层模块不应该依赖下层模块
                constraints.append(f"❌ 违反依赖方向: {mod}(L{layer}) 不应依赖 {dep}(L{dep_layer})")

    # 循环依赖检测
    for mod, arch in module_arch.items():
        for dep in arch.get("dependencies", []):
            dep_arch = module_arch.get(dep, {})
            if mod in dep_arch.get("dependencies", []):
                constraints.append(f"⚠️ 循环依赖: {mod} <-> {dep}")

    # 稳定性约束
    high_stability = [mod for mod, arch in module_arch.items() if arch.get("stability_score", 0) > 0.5]
    if high_stability:
        constraints.append(f"稳定模块(被依赖>50%): {', '.join(high_stability[:5])}")
        constraints.append("稳定模块应优先设计接口，变更需谨慎")

    return constraints


def _analyze_module_health(
    modules: dict[str, dict[str, Any]],
    module_arch: dict[str, dict[str, Any]],
    files_payload: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """分析每个模块的健康状况，识别需要重构的模块。"""
    health: dict[str, dict[str, Any]] = {}

    for mod, payload in modules.items():
        arch = module_arch.get(mod, {})
        files = payload.get("files", [])
        file_count = len(files)

        # 计算复杂度指标
        complexity_score = 0
        concerns = []

        # 1. 文件数量过多（可能职责过多）
        if file_count > 20:
            complexity_score += 2
            concerns.append(f"文件过多({file_count})，建议拆分模块")
        elif file_count > 10:
            complexity_score += 1

        # 2. 循环依赖
        deps = set(arch.get("dependencies", []))
        dependents = set(arch.get("dependents", []))
        cycles = deps & dependents
        if cycles:
            complexity_score += 3
            concerns.append(f"与 {len(cycles)} 个模块存在循环依赖")

        # 3. 未解析依赖过多
        unresolved = payload.get("unresolved_dependencies", [])
        if len(unresolved) > 3:
            complexity_score += 1
            concerns.append(f"未解析依赖过多({len(unresolved)})")

        # 4. 稳定性与变更风险
        stability = arch.get("stability_score", 0)
        if stability > 0.7 and file_count > 15:
            concerns.append("高稳定性但体积大，重构影响面广")

        # 5. 层级跳跃（直接依赖非相邻层级）
        layer = arch.get("layer", 0)
        for dep in deps:
            dep_arch = module_arch.get(dep, {})
            dep_layer = dep_arch.get("layer", 0)
            if abs(dep_layer - layer) > 1:
                concerns.append(f"层级跳跃: 依赖 L{dep_layer} 的 {dep}")

        # 健康评级
        if complexity_score >= 4:
            rating = "critical"
        elif complexity_score >= 2:
            rating = "warning"
        elif concerns:
            rating = "fair"
        else:
            rating = "healthy"

        health[mod] = {
            "complexity_score": complexity_score,
            "file_count": file_count,
            "concerns": concerns[:5],
            "health_rating": rating,
            "recommendation": _generate_health_recommendation(rating, concerns),
        }

    return health


def _generate_health_recommendation(rating: str, concerns: list[str]) -> str:
    """根据健康状况生成建议。"""
    if rating == "healthy":
        return "保持良好实践"
    if rating == "critical":
        return "需要重构: " + concerns[0] if concerns else "复杂度过高，建议拆分"
    if rating == "warning":
        return "关注: " + concerns[0] if concerns else "存在一些架构问题"
    return "建议优化"


def _detect_architecture_smells(
    modules: dict[str, dict[str, Any]],
    module_arch: dict[str, dict[str, Any]],
    module_health: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """检测架构坏味道（Architecture Smells）。"""
    smells = []

    for mod, health in module_health.items():
        if health["health_rating"] == "critical":
            smells.append(
                {
                    "type": "god_module",
                    "module": mod,
                    "severity": "high",
                    "description": f"模块 '{mod}' 过于庞大或复杂",
                    "suggestion": "拆分为更小、职责单一的模块",
                }
            )

    # 检测不稳定的依赖（不稳定模块被依赖）
    for mod, arch in module_arch.items():
        instability = 1 - arch.get("stability_score", 0)
        if instability > 0.7:  # 不稳定（经常变化）
            dependents = arch.get("dependents", [])
            if len(dependents) > 2:
                smells.append(
                    {
                        "type": "unstable_dependency",
                        "module": mod,
                        "severity": "medium",
                        "description": f"不稳定模块 '{mod}' 被 {len(dependents)} 个模块依赖",
                        "suggestion": "引入抽象层或门面模式隔离变化",
                    }
                )

    # 检测知识过载（一个模块知道太多其他模块的内部）
    for mod, arch in module_arch.items():
        deps = arch.get("dependencies", [])
        if len(deps) > 6:
            smells.append(
                {
                    "type": "knowledge_overload",
                    "module": mod,
                    "severity": "medium",
                    "description": f"模块 '{mod}' 依赖 {len(deps)} 个其他模块",
                    "suggestion": "考虑引入中介者模式或事件驱动解耦",
                }
            )

    return smells


def _plan_module_evolution(
    tasks: list[dict[str, Any]],
    modules: dict[str, dict[str, Any]],
    module_arch: dict[str, dict[str, Any]],
    smells: list[dict[str, Any]],
    pm_iteration: int,
) -> dict[str, Any]:
    """基于任务和现有架构规划模块演进。"""
    planned_modules = []
    restructuring = []
    deprecated = []
    decisions = []

    # 分析任务目标，推断可能需要的新模块
    task_goals = " ".join(str(t.get("goal", "") or t.get("title", "")).lower() for t in tasks if isinstance(t, dict))

    # 1. 检测是否需要新模块
    new_module_keywords = {
        "auth": ("auth", ["authentication", "auth", "login", "oauth", "jwt"]),
        "payment": ("payment", ["payment", "billing", "stripe", "invoice"]),
        "notification": ("notification", ["notification", "email", "sms", "push"]),
        "search": ("search", ["search", "elasticsearch", "filter", "query"]),
        "cache": ("cache", ["cache", "redis", "memoization"]),
        "queue": ("queue", ["queue", "worker", "background job", "celery", "rq"]),
        "api": ("api", ["rest api", "graphql", "endpoint", "controller"]),
        "cli": ("cli", ["command line", "cli", "terminal", "shell"]),
    }

    existing_module_names = set(modules.keys())

    for module_name, (short_name, keywords) in new_module_keywords.items():
        if any(kw in task_goals for kw in keywords) and not any(
            module_name in em or em in module_name for em in existing_module_names
        ):
            planned_modules.append(
                {
                    "module_id": f"planned_{module_name}",
                    "name": module_name,
                    "suggested_location": f"src/{module_name}" if "src" in existing_module_names else module_name,
                    "reason": f"任务涉及 {'/'.join(keywords[:2])} 功能",
                    "estimated_files": 3,
                    "depends_on": ["core", "models"] if "models" in existing_module_names else [],
                    "priority": "high" if any(kw in task_goals for kw in keywords[:2]) else "medium",
                    "suggested_layer": 2,  # 应用服务层
                }
            )

            decisions.append(
                {
                    "decision_id": f"ADR-{pm_iteration:03d}-{module_name.upper()}",
                    "title": f"新增 {module_name} 模块",
                    "context": f"任务分析显示需要 {module_name} 功能",
                    "decision": f"创建独立的 {module_name} 模块，位于应用服务层(L2)",
                    "consequences": [
                        "清晰的职责分离",
                        f"其他模块可通过标准接口使用 {module_name}",
                        f"需要定义 {module_name} 的公共 API",
                    ],
                    "status": "proposed",
                    "created_at": _utc_now_iso(),
                    "related_modules": [module_name],
                }
            )

    # 2. 基于坏味道生成重组建议
    for smell in smells:
        if smell["type"] == "god_module":
            mod = smell["module"]

            # 建议拆分
            restructuring.append(
                {
                    "action": "split",
                    "target_module": mod,
                    "reason": smell["description"],
                    "source_modules": [mod],
                    "new_modules_suggested": [
                        f"{mod}_core",
                        f"{mod}_impl",
                        f"{mod}_utils",
                    ],
                    "impact_tasks": [],
                    "suggestion": f"将 '{mod}' 拆分为核心接口、实现细节和工具函数",
                }
            )

        elif smell["type"] == "unstable_dependency":
            mod = smell["module"]
            restructuring.append(
                {
                    "action": "introduce_facade",
                    "target_module": mod,
                    "reason": smell["description"],
                    "suggestion": f"为 '{mod}' 创建稳定的外观接口",
                }
            )

    # 3. 检测可能的废弃模块（长时间没有文件变更的模块）
    # 这里简化处理：文件数量少的模块
    for mod, payload in modules.items():
        if len(payload.get("files", [])) <= 1 and not module_arch.get(mod, {}).get("dependents", []):
            deprecated.append(
                {
                    "module": mod,
                    "reason": "文件少且无其他模块依赖，可能已废弃",
                    "suggestion": "考虑合并到父模块或明确废弃",
                }
            )

    return {
        "planned_modules": planned_modules,
        "restructuring": restructuring,
        "deprecated_modules": deprecated,
        "architecture_decisions": decisions,
    }


def _generate_evolution_roadmap(
    module_health: dict[str, dict[str, Any]],
    planned_modules: list[dict[str, Any]],
    restructuring: list[dict[str, Any]],
    module_order: list[str],
) -> list[dict[str, Any]]:
    """生成架构演进路线图。"""
    roadmap = []

    # 阶段1: 基础设施稳定化（最底层模块）
    base_modules = [m for m in module_order[:3]]
    if base_modules:
        roadmap.append(
            {
                "phase": 1,
                "name": "基础设施稳定化",
                "focus": "底层模块",
                "modules": base_modules,
                "actions": ["完善接口", "添加测试", "文档化"],
                "rationale": "底层模块影响面广，需优先稳定",
            }
        )

    # 阶段2: 处理架构坏味道
    critical_modules = [m for m, h in module_health.items() if h["health_rating"] == "critical"]
    if critical_modules:
        roadmap.append(
            {
                "phase": 2,
                "name": "重构关键模块",
                "focus": "架构健康",
                "modules": critical_modules[:3],
                "actions": ["拆分大模块", "消除循环依赖", "简化接口"],
                "rationale": "解决架构技术债务",
            }
        )

    # 阶段3: 新增模块
    if planned_modules:
        roadmap.append(
            {
                "phase": 3,
                "name": "功能扩展",
                "focus": "新功能模块",
                "modules": [m["name"] for m in planned_modules[:3]],
                "actions": ["创建模块", "定义API", "集成测试"],
                "rationale": "支持新功能需求",
            }
        )

    # 阶段4: 上层优化
    if len(module_order) > 3:
        roadmap.append(
            {
                "phase": 4,
                "name": "应用层优化",
                "focus": "上层模块",
                "modules": module_order[-3:],
                "actions": ["性能优化", "用户体验改进"],
                "rationale": "在稳定基础上优化用户体验",
            }
        )

    return sorted(roadmap, key=lambda x: x["phase"])


def _merge_with_existing_blueprint(
    new_analysis: dict[str, Any],
    existing_blueprint: dict[str, Any],
    pm_iteration: int,
) -> dict[str, Any]:
    """将新分析与现有蓝图合并，保持架构决策的连续性。"""
    merged = dict(new_analysis)

    if not isinstance(existing_blueprint, dict):
        return merged

    # 保留已接受的架构决策
    existing_decisions = existing_blueprint.get("architecture_decisions", [])
    new_decisions = merged.get("architecture_decisions", [])

    accepted_decisions = [d for d in existing_decisions if isinstance(d, dict) and d.get("status") == "accepted"]

    # 合并决策列表，保留历史
    decision_ids = {d.get("decision_id") for d in new_decisions if isinstance(d, dict)}
    for d in accepted_decisions:
        if isinstance(d, dict) and d.get("decision_id") not in decision_ids:
            merged.setdefault("architecture_decisions", []).append(d)

    # 保留已计划的模块（除非已实现）
    existing_planned = existing_blueprint.get("planned_modules", [])
    new_modules = {m.get("name") for m in merged.get("planned_modules", []) if isinstance(m, dict)}

    for m in existing_planned:
        if isinstance(m, dict) and m.get("name") not in new_modules:
            # 检查是否已实现（存在于 files 中）
            module_path = m.get("suggested_location", "")
            if not any(module_path in f for f in merged.get("files", {})):
                merged.setdefault("planned_modules", []).append(m)

    # 更新迭代信息
    merged["evolution_iteration"] = pm_iteration
    merged["previous_iteration"] = existing_blueprint.get("run_id")

    return merged


def _load_existing_blueprint(path: str) -> dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (RuntimeError, ValueError):
        return {}


def run_chief_engineer_analysis(
    *,
    tasks: Any,
    workspace_full: str,
    run_id: str,
    pm_iteration: int,
    run_blueprint_path: str,
    runtime_blueprint_path: str,
) -> dict[str, Any]:
    """Analyze director tasks and update deterministic project blueprint artifacts."""
    director_tasks = _active_director_tasks(tasks)
    existing = _load_existing_blueprint(runtime_blueprint_path) or _load_existing_blueprint(run_blueprint_path)
    files_payload: dict[str, Any] = (
        dict(cast("dict[str, Any]", existing.get("files"))) if isinstance(existing.get("files"), dict) else {}
    )
    tasks_payload: dict[str, Any] = (
        dict(cast("dict[str, Any]", existing.get("tasks"))) if isinstance(existing.get("tasks"), dict) else {}
    )
    task_updates: list[dict[str, Any]] = []
    task_update_map: dict[str, dict[str, Any]] = {}

    for task in director_tasks:
        task_id = str(task.get("id") or "").strip()
        title = str(task.get("title") or "").strip()
        goal = str(task.get("goal") or "").strip()
        target_files = [
            path for path in normalize_path_list(task.get("target_files") or []) if _is_valid_target_file(path)
        ]
        scope_paths = normalize_path_list(task.get("scope_paths") or task.get("scope") or [])
        scan_files = _dedupe(
            [path for path in target_files if not _is_directory_like(path)]
            + _expand_scope_files(workspace_full, scope_paths)
        )

        missing_targets: list[str] = []
        unresolved_imports: list[str] = []
        missing_dependency_hints: list[str] = []

        # 代码智能：增量语义分析
        semantic_context: dict[str, Any] = {}
        incremental_analysis: dict[str, Any] = {}

        if CODE_INTEL_AVAILABLE and task_id:
            try:
                task_desc = goal or title or task_id

                # 1. 使用增量分析器检测和分析变更
                analyzer = IncrementalSemanticAnalyzer(workspace_full)
                changes = analyzer.detect_changes(scan_files)

                if changes:
                    # 只分析变更的文件
                    incremental_analysis = analyzer.analyze_incremental(
                        changes=changes,
                        task_description=task_desc,
                    )

                    # 将增量分析发现的影响文件加入扫描
                    affected = incremental_analysis.get("affected_files", [])
                    for rel_path in affected:
                        if rel_path not in scan_files:
                            scan_files.append(rel_path)

                # 2. 获取完整的语义上下文（用于LLM提示）
                code_intel = CodeIntelligenceService(workspace_full)
                semantic_context = code_intel.get_context_for_task(
                    task_description=task_desc,
                    changed_files=target_files,
                    budget_override={"top_n_files": 15, "max_chars": 16000},
                )

                # 合并增量分析结果
                semantic_context["incremental_analysis"] = incremental_analysis

                # 3. 将语义发现的相关文件加入扫描列表
                for f in semantic_context.get("top_files", []):
                    rel_path = f.get("path", "")
                    if rel_path and rel_path not in scan_files:
                        scan_files.append(rel_path)

            except (RuntimeError, ValueError) as exc:
                logger.debug("semantic index lookup failed (non-critical): %s", exc)

        for target in target_files:
            if _is_directory_like(target):
                continue
            full = os.path.join(workspace_full, target)
            if not os.path.exists(full) and target not in missing_targets:
                missing_targets.append(target)

        for rel_path in scan_files:
            file_payload, dependency_hints = _analyze_file(workspace_full, rel_path)
            files_payload[file_payload.path] = asdict(file_payload)
            for token in file_payload.unresolved_imports:
                if token not in unresolved_imports:
                    unresolved_imports.append(token)
            for hint in dependency_hints:
                if hint not in missing_dependency_hints:
                    missing_dependency_hints.append(hint)

        unresolved_sources = _dedupe([token.split(":", 1)[0] for token in unresolved_imports if ":" in token])
        scope_for_apply = _dedupe(
            scope_paths + target_files + missing_targets + missing_dependency_hints + unresolved_sources
        )
        verify_ready = not missing_targets and not unresolved_imports
        if verify_ready:
            continue_reason = "verify_ready"
        elif missing_targets:
            continue_reason = f"missing_target_files:{len(missing_targets)}"
        else:
            continue_reason = f"unresolved_imports:{len(unresolved_imports)}"

        # 提取增量分析结果
        affected_files = incremental_analysis.get("affected_files", []) if incremental_analysis else []
        risk_level = (
            incremental_analysis.get("impact_analysis", {}).get("risk_level", "unknown")
            if incremental_analysis
            else "unknown"
        )

        task_blueprint = TaskBlueprint(
            task_id=task_id,
            title=title,
            goal=goal,
            target_files=target_files,
            scope_paths=scope_paths,
            missing_targets=missing_targets,
            unresolved_imports=unresolved_imports,
            scope_for_apply=scope_for_apply,
            verify_ready=verify_ready,
            continue_reason=continue_reason,
            updated_at=_utc_now_iso(),
            context_pack=semantic_context,
            semantic_files=[f.get("path", "") for f in semantic_context.get("top_files", [])],
            incremental_analysis=incremental_analysis,
            affected_files=affected_files,
            risk_level=risk_level,
        )
        task_payload = asdict(task_blueprint)
        task_payload["construction_plan"] = _build_task_construction_plan(
            task=task,
            task_blueprint=task_payload,
            file_payload_map=files_payload,
        )
        if task_id:
            tasks_payload[task_id] = task_payload
            task_update_map[task_id] = task_payload
        task_updates.append(task_payload)

    modules_payload = _build_modules_from_files(files_payload)

    # 生成全局架构上下文
    module_order = _topological_sort_modules(modules_payload)
    api_contracts = _detect_api_contracts(modules_payload, files_payload)
    data_flows = _build_data_flows(modules_payload, files_payload)
    module_architecture = _build_module_architecture(modules_payload, files_payload, module_order)
    architecture_constraints = _build_architecture_constraints(module_architecture, modules_payload)

    # 架构演进分析
    module_health = _analyze_module_health(modules_payload, module_architecture, files_payload)
    architecture_smells = _detect_architecture_smells(modules_payload, module_architecture, module_health)
    evolution_plan = _plan_module_evolution(
        director_tasks, modules_payload, module_architecture, architecture_smells, pm_iteration
    )
    evolution_roadmap = _generate_evolution_roadmap(
        module_health,
        evolution_plan.get("planned_modules", []),
        evolution_plan.get("restructuring", []),
        module_order,
    )

    verify_ready_count = sum(1 for item in task_updates if isinstance(item, dict) and bool(item.get("verify_ready")))
    blueprint = ProjectBlueprint(
        workspace=workspace_full,
        run_id=run_id,
        pm_iteration=int(pm_iteration or 0),
        updated_at=_utc_now_iso(),
        files=files_payload,
        modules=modules_payload,
        tasks=tasks_payload,
        stats={
            "director_task_count": len(director_tasks),
            "task_update_count": len(task_updates),
            "verify_ready_count": verify_ready_count,
            "construction_file_plan_count": sum(
                len((item.get("construction_plan") or {}).get("file_plans") or [])
                for item in task_updates
                if isinstance(item, dict)
            ),
            "construction_method_count": sum(
                len((item.get("construction_plan") or {}).get("method_catalog") or [])
                for item in task_updates
                if isinstance(item, dict)
            ),
            "missing_targets_total": sum(
                len(item.get("missing_targets") or []) for item in task_updates if isinstance(item, dict)
            ),
            "unresolved_imports_total": sum(
                len(item.get("unresolved_imports") or []) for item in task_updates if isinstance(item, dict)
            ),
            "module_count": len(modules_payload),
            "file_count": len(files_payload),
            "architecture_layer_count": len(set(a.get("layer", 0) for a in module_architecture.values())),
            "api_contract_count": len(api_contracts),
            "data_flow_count": len(data_flows),
        },
        module_order=module_order,
        api_contracts=api_contracts,
        data_flows=data_flows,
        module_architecture=module_architecture,
        architecture_constraints=architecture_constraints,
        # 架构演进规划
        planned_modules=evolution_plan.get("planned_modules", []),
        deprecated_modules=[d.get("module") for d in evolution_plan.get("deprecated_modules", [])],
        architecture_decisions=evolution_plan.get("architecture_decisions", []),
        module_restructuring=evolution_plan.get("restructuring", []),
        evolution_roadmap=evolution_roadmap,
    )

    # 合并现有蓝图保持连续性
    blueprint_payload = asdict(blueprint)
    blueprint_payload = _merge_with_existing_blueprint(blueprint_payload, existing, pm_iteration)
    if run_blueprint_path:
        write_json_atomic(run_blueprint_path, blueprint_payload)
    if runtime_blueprint_path:
        write_json_atomic(runtime_blueprint_path, blueprint_payload)

    # 在 task_update 中注入架构演进信息供下游使用
    for task_id in task_update_map:
        task_update_map[task_id]["architecture_context"] = {
            "module_order": module_order,
            "module_health": {
                k: v for k, v in module_health.items() if k in task_update_map.get(task_id, {}).get("scope_paths", [])
            },
            "evolution_roadmap": evolution_roadmap[:2],  # 只给前两个阶段
            "planned_modules": evolution_plan.get("planned_modules", []),
            "architecture_decisions": evolution_plan.get("architecture_decisions", []),
        }

    return {
        "schema_version": 1,
        "role": "ChiefEngineer",
        "ran": True,
        "hard_failure": False,
        "reason": "chief_engineer_updated",
        "summary": f"ChiefEngineer updated blueprint for {len(task_updates)} director task(s).",
        "blueprint_path": run_blueprint_path,
        "runtime_blueprint_path": runtime_blueprint_path,
        "task_update_count": len(task_updates),
        "task_updates": task_updates,
        "task_update_map": task_update_map,
        "stats": blueprint_payload.get("stats", {}),
        "architecture_evolution": {
            "module_health_summary": {mod: h["health_rating"] for mod, h in module_health.items()},
            "smells_detected": len(architecture_smells),
            "smells": [
                {"type": s["type"], "module": s["module"], "severity": s["severity"]} for s in architecture_smells[:5]
            ],
            "planned_modules_count": len(evolution_plan.get("planned_modules", [])),
            "planned_modules": [m["name"] for m in evolution_plan.get("planned_modules", [])],
            "deprecated_modules": [d.get("module") for d in evolution_plan.get("deprecated_modules", [])],
            "roadmap_phases": len(evolution_roadmap),
            "architecture_decisions_count": len(evolution_plan.get("architecture_decisions", [])),
        },
    }


def run_chief_engineer_task(
    *,
    task: dict[str, Any],
    workspace_full: str,
    cache_root_full: str,
    run_id: str,
    pm_iteration: int,
) -> dict[str, Any]:
    """Run ChiefEngineer analysis for a single PM task."""
    from polaris.delivery.cli.pm.director_mgmt import build_run_dir

    run_dir = build_run_dir(workspace_full, cache_root_full, int(pm_iteration or 0))
    run_blueprint_path = os.path.join(run_dir, "contracts", "chief_engineer.blueprint.json")
    runtime_blueprint_path = resolve_artifact_path(
        workspace_full,
        cache_root_full,
        "runtime/contracts/chief_engineer.blueprint.json",
    )
    analysis = run_chief_engineer_analysis(
        tasks=[task],
        workspace_full=workspace_full,
        run_id=run_id,
        pm_iteration=pm_iteration,
        run_blueprint_path=run_blueprint_path,
        runtime_blueprint_path=runtime_blueprint_path,
    )
    task_id = str(task.get("id") or "").strip()
    task_update_map_raw = analysis.get("task_update_map")
    task_update_map: dict[str, Any] = task_update_map_raw if isinstance(task_update_map_raw, dict) else {}
    raw_task_update = task_update_map.get(task_id) if task_id else None
    task_update: dict[str, Any] = raw_task_update if isinstance(raw_task_update, dict) else {}
    return {
        "ok": not bool(analysis.get("hard_failure")),
        "summary": str(analysis.get("summary") or "").strip(),
        "analysis": analysis,
        "task_update": task_update,
    }


__all__ = [
    "ApiContract",
    "ArchitectureDecision",
    "BlueprintFile",
    "DataFlowEdge",
    "ModuleArchitecture",
    "ModuleRestructuring",
    "ProjectBlueprint",
    "TaskBlueprint",
    "run_chief_engineer_analysis",
    "run_chief_engineer_task",
]
