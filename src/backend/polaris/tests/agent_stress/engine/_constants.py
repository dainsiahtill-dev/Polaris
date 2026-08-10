"""Shared constants for agent_stress StressEngine package."""

# mypy: ignore-errors

import re

MAX_NON_LLM_CONTROL_PLANE_STALL_SECONDS = 120.0
DEFAULT_MIN_NEW_CODE_FILES = 2
DEFAULT_MIN_NEW_CODE_LINES = 80
DEFAULT_CONTROL_PLANE_RETRY_ATTEMPTS = 3
DEFAULT_CONTROL_PLANE_RETRY_BACKOFF_SECONDS = 0.5
RETRYABLE_HTTP_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
COMPLETED_ROLE_STATUSES = {"completed", "success", "done"}
FAILED_ROLE_STATUSES = {"failed", "error", "cancelled", "blocked", "timeout"}
FALLBACK_SCAFFOLD_SIGNATURES = (
    "Auto-generated starter entrypoint for Polaris stress workflow",
    "This scaffold was auto-generated because Director completed without file output",
    "Generated Project Scaffold",
    "Execute ready tasks",
)
PLACEHOLDER_CODE_SIGNATURES = (
    ("todo", re.compile(r"\bTODO\b", re.IGNORECASE)),
    ("fixme", re.compile(r"\bFIXME\b", re.IGNORECASE)),
    ("tbd", re.compile(r"\bTBD\b", re.IGNORECASE)),
    ("not_implemented", re.compile(r"\bNotImplemented(?:Error|Exception)?\b", re.IGNORECASE)),
    ("empty_business_logic", re.compile(r"实现核心业务逻辑|核心逻辑待实现|业务逻辑待实现", re.IGNORECASE)),
    ("placeholder", re.compile(r"\bplaceholder\b", re.IGNORECASE)),
    ("stub", re.compile(r"\bstub\b", re.IGNORECASE)),
)
GENERIC_SCAFFOLD_MARKERS = (
    "项目主入口模块",
    "通用工具函数模块",
    "helpers 模块的单元测试",
    "def safe_divide(",
    "def parse_arguments(",
    "应用程序主入口点",
)
PYTHON_EMPTY_FUNCTION_FALLBACK_PATTERN = re.compile(
    r"def\s+(?P<name>[A-Za-z_]\w*)\s*\([^)]*\)\s*:\s*\n[ \t]+(?:pass|\.{3}|#\s*\.{3})[ \t]*(?:\n|$)",
    re.MULTILINE,
)
JS_TS_EMPTY_FUNCTION_PATTERN = re.compile(
    r"(?:function\s+[A-Za-z_$][\w$]*\s*\([^)]*\)\s*\{\s*\}"
    r"|(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*\([^)]*\)\s*=>\s*\{\s*\})",
    re.MULTILINE,
)
DOMAIN_KEYWORD_STOPWORDS = {
    "app",
    "application",
    "code",
    "core",
    "data",
    "demo",
    "helper",
    "main",
    "module",
    "project",
    "script",
    "service",
    "system",
    "test",
    "tool",
    "unit",
    "utils",
    "项目",
    "功能",
    "工具",
    "模块",
    "应用",
    "测试",
    "系统",
    "配置",
    "管理",
    "脚本",
    "数据",
}
MIN_GENERIC_SCAFFOLD_MARKERS = 2
MIN_CROSS_PROJECT_DUPLICATE_FILES = 3
MIN_CROSS_PROJECT_DUPLICATE_RATIO = 0.8
STAGE_NAME_TO_CHAIN_ROLE = {
    "docs_generation": "architect",
    "pm_planning": "pm",
    "director_dispatch": "director",
    "quality_gate": "qa",
    "chief_engineer_review": "chief_engineer",
    "chief_engineer": "chief_engineer",
}
PROJECT_CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".cs",
    ".cpp",
    ".c",
    ".h",
    ".html",
    ".css",
    ".scss",
    ".vue",
    ".svelte",
    ".kt",
    ".swift",
    ".php",
    ".rb",
    ".sh",
    ".ps1",
}
IGNORED_WORKSPACE_ROOTS = {
    ".polaris",
    "stress_reports",
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
}
