"""PM 适配器文本工具与冻结常量（叶子模块）.

承载 PM 合同解析/归一化/合成所依赖的冻结常量、正则表达式与纯函数。
本模块为基础叶子：不依赖任何兄弟 mixin，可被其它 PM 模块自由导入。

行为与原 ``pm_adapter.py`` 中对应符号 100% 一致（无损迁移）。
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

_DEFAULT_PHASE_SEQUENCE = ("requirements", "implementation", "verification")
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "task",
    "tasks",
    "feature",
    "project",
    "module",
    "system",
    "please",
    "need",
    "build",
    "implement",
    "create",
    "develop",
}
_ACTION_MARKERS = ("implement", "build", "define", "design", "create", "实现", "构建", "设计", "编写", "定义")
_TASK_LINE_PREFIX = re.compile(r"^(?:[-*]\s+|\d+[.)]\s+|[（(]?\d+[）)]\s+)")
_PM_TASK_LABEL_PREFIX = re.compile(
    r"^\s*(?:task|任务|pm[-_ ]*task)\s*[-_#]*\s*\d+\s*(?:[:：.\-—–]\s*)?",
    re.IGNORECASE,
)
_PM_TASK_LABEL_SUFFIX = re.compile(
    r"\s*[（(]\s*(?:task|任务|pm[-_ ]*task)\s*[-_#]*\s*\d+\s*[）)]\s*$",
    re.IGNORECASE,
)
_PM_DETAIL_BULLET_PREFIX = re.compile(
    r"^\s*(?:目标|范围|验收标准|验收|依赖|依赖链|风险点|全局风险|当前状态|说明|description|goal|scope|acceptance|dependencies)\s*[:：]",
    re.IGNORECASE,
)
_TASK_SECTION_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:task|任务)(?:\s*[-_ ]*(\d+)\s*(?:[:：.\-]\s*)?|\s*[:：]\s*)(.*?)\s*$",
    re.IGNORECASE,
)
_PM_SCOPE_PATH_ROOTS = {
    "app",
    "backend",
    "components",
    "docs",
    "electron",
    "frontend",
    "lib",
    "packages",
    "scripts",
    "src",
    "tests",
    "workspace",
}
_PM_SCOPE_PATH_FILENAMES = {
    "package.json",
    "README.md",
    "tsconfig.json",
    "vite.config.ts",
    "vitest.config.ts",
    "tailwind.config.js",
    "postcss.config.js",
    "pyproject.toml",
}
_PM_SCOPE_PATH_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
_PM_NON_PATH_SCOPE_RE = re.compile(r"[\s,，、；;：:。]|[\u4e00-\u9fff]")
_PM_ROOT_WORKSPACE_HINT_RE = re.compile(
    r"(?:工作区根|仓库根|项目根|workspace\s+root|repo(?:sitory)?\s+root|root\s+directory)",
    re.IGNORECASE,
)
_PM_PYTHON_HINT_RE = re.compile(r"(?:\bpython\b|标准库|pytest|命令行|cli\b)", re.IGNORECASE)
_PM_TEST_CONTRACT_HINT_RE = re.compile(
    r"(?:\btests?\b|\btesting\b|\bpytest\b|\bnpm\s+test\b|单元测试|集成测试|测试|验证|验收|回归)",
    re.IGNORECASE,
)
_PM_README_HINT_RE = re.compile(r"(?:\breadme(?:\.md)?\b|运行说明|说明如何运行)", re.IGNORECASE)
_PM_SOURCE_FILE_HINT_RE = re.compile(
    r"(?:源码|代码文件|真实代码文件|source\s+file|code\s+file|implementation|可运行)",
    re.IGNORECASE,
)
_PM_BARE_FILENAME_HINT_RE = re.compile(r"`?([A-Za-z][A-Za-z0-9_-]{2,})(?:\.py)?`?")
_PM_EXPLICIT_FILE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])"
    r"([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*"
    r"\.(?:css|html|js|jsx|json|md|mjs|py|toml|ts|tsx|yaml|yml))"
    r"(?![A-Za-z0-9_./-])",
    re.IGNORECASE,
)
_PM_PROMPT_DIRECTIVE_MAX_CHARS = 18_000
_PM_RETRY_DIRECTIVE_MAX_CHARS = 6_000
_PM_CONTRACT_SCOPE_PATH_LIMIT = 6
_PM_PLAN_DIRECTIVE_REDACTED = "[redacted planning context; source docs retained separately]"
_PM_PLAN_FORBIDDEN_TEXT_REPLACEMENTS = (
    (re.compile(r"\byou\s+are\b", re.IGNORECASE), "operator context"),
    (re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE), "runtime instruction"),
    (re.compile(r"\bno\s+yapping\b", re.IGNORECASE), "concise mode"),
    (re.compile(r"\brole(s)?\b", re.IGNORECASE), "responsibility"),
    (re.compile(r"<\s*/?\s*thinking\s*>", re.IGNORECASE), "[redacted]"),
    (re.compile(r"<\s*/?\s*tool_call\s*>", re.IGNORECASE), "[redacted]"),
)
_PM_PROMPT_ECHO_MARKERS = (
    "Failed to parse action:",
    "你是 Polaris PM",
    "请仅输出 JSON，格式如下",
    "禁止返回 Markdown",
)
_PM_SCHEMA_PLACEHOLDER_VALUES = {
    "任务标题",
    "该任务目标",
    "执行背景与约束",
    "变更范围摘要",
    "步骤1",
    "步骤2",
    "可测验收1",
    "可测验收2",
    "task title",
    "task goal",
    "task description",
    "untitled task",
}
_PM_META_DIAGNOSTIC_TITLES = {
    "事实已补齐",
    "任务数",
    "当前任务数",
    "待生成任务",
    "待生成蓝图",
}
_PM_META_DIAGNOSTIC_TEXT_RE = re.compile(
    r"(?:"
    r"requirements\.md\s*已读取.*需求边界清晰|"
    r"需求边界清晰.*requirements\.md\s*已读取|"
    r"需新建\s*\d+\s*个任务.*依赖链|"
    r"当前任务数\s*[:：]?\s*\d+|"
    r"任务数\s*[:：]?\s*\d+"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_PM_NON_DELIVERY_CONSTRAINT_TEXT_RE = re.compile(
    r"(?:"
    r"^无现有代码基\s*[,，]?\s*需从零构建$|"
    r"验收维度强调|"
    r"教学[/／]考核点|"
    r"必须形成依赖链|"
    r"避免并行冲突|"
    r"^design$|"
    r"^执行至少\s*\d+\s*组测试用例.*全部通过$"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def _pm_text(value: Any) -> str:
    return str(value or "").strip()


def _pm_strip_markdown_title_noise(value: Any) -> str:
    text = _pm_text(value)
    if not text:
        return ""
    text = re.sub(r"[*_`]{2,}", "", text)
    text = text.strip(" \t\r\n*_`")
    return re.sub(r"\s+", " ", text).strip()


def _pm_strip_task_label_prefix(value: Any) -> str:
    text = _pm_strip_markdown_title_noise(value)
    if not text:
        return ""
    return _pm_strip_markdown_title_noise(_PM_TASK_LABEL_PREFIX.sub("", text, count=1))


def _pm_title_fragment(value: Any) -> str:
    text = _pm_strip_task_label_prefix(value)
    if not text:
        return ""
    fragment = re.split(r"\s+[—–-]\s+|[。.;；\n]", text, maxsplit=1)[0]
    fragment = _PM_TASK_LABEL_SUFFIX.sub("", fragment)
    return _pm_strip_markdown_title_noise(fragment)


def _pm_extract_inline_list_field(text: str, field_name: str) -> list[str]:
    source = str(text or "")
    if not source:
        return []
    field = re.escape(field_name)
    pattern = re.compile(
        rf"(?:^|\s+-\s+)\s*[-*]?\s*\*\*{field}\*\*\s*[:：]\s*(?P<value>.*?)(?=\s+-\s+\*\*[A-Za-z_\u4e00-\u9fff ]+\*\*\s*[:：]|\s+---|$)",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(source)
    if not match:
        return []
    value = str(match.group("value") or "").strip()
    if not value:
        return []
    value = value.strip("`").strip()
    if value.startswith("["):
        end = value.find("]")
        if end >= 0:
            value = value[: end + 1]
        try:
            parsed = json.loads(value)
        except (RuntimeError, ValueError):
            try:
                parsed = ast.literal_eval(value)
            except (RuntimeError, SyntaxError, ValueError):
                parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]


def _pm_is_dependency_chain_text(value: Any) -> bool:
    text = _pm_strip_markdown_title_noise(value)
    if not text:
        return False
    task_refs = re.findall(r"\bTASK\s*[-_#]?\s*\d+\b", text, flags=re.IGNORECASE)
    return len(task_refs) >= 2 and ("→" in text or "->" in text or "=>" in text)


def _pm_raw_task_is_dependency_chain(raw: dict[str, Any]) -> bool:
    return any(
        _pm_is_dependency_chain_text(raw.get(key)) for key in ("title", "subject", "goal", "description", "backlog_ref")
    )


def _pm_flatten_raw_path_values(value: Any) -> list[str]:
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]
    if value is None:
        return []
    token = str(value).strip()
    return [token] if token else []


def _pm_raw_task_has_explicit_concrete_target(raw: dict[str, Any]) -> bool:
    candidates: list[str] = []
    for key in ("target_files", "files"):
        candidates.extend(_pm_flatten_raw_path_values(raw.get(key)))
    inline_source = "\n".join(
        str(raw.get(key) or "") for key in ("description", "goal", "scope") if raw.get(key) is not None
    )
    candidates.extend(_pm_extract_inline_list_field(inline_source, "target_files"))
    concrete_targets, _scope_paths = _pm_split_concrete_targets_and_scopes(candidates)
    return bool(concrete_targets)


def _pm_raw_task_is_non_delivery_constraint(raw: dict[str, Any]) -> bool:
    if _pm_raw_task_has_explicit_concrete_target(raw):
        return False
    text = "\n".join(
        _pm_title_fragment(raw.get(key) or "")
        for key in ("title", "subject", "goal", "description", "backlog_ref")
        if raw.get(key) is not None
    )
    text_without_action = re.sub(r"^(?:实现|完成|补齐)\s*", "", text).strip()
    return bool(
        _PM_NON_DELIVERY_CONSTRAINT_TEXT_RE.search(text)
        or _PM_NON_DELIVERY_CONSTRAINT_TEXT_RE.search(text_without_action)
    )


def _pm_raw_task_is_meta_diagnostic(raw: dict[str, Any]) -> bool:
    title = _pm_title_fragment(raw.get("title") or raw.get("subject") or "")
    title_without_action = re.sub(r"^(?:实现|完成|补齐)\s*", "", title).strip()
    if title_without_action in _PM_META_DIAGNOSTIC_TITLES:
        return True

    text = "\n".join(
        str(raw.get(key) or "")
        for key in ("title", "subject", "goal", "description", "scope", "backlog_ref")
        if raw.get(key) is not None
    )
    return bool(_PM_META_DIAGNOSTIC_TEXT_RE.search(text))


def _pm_is_prompt_echo_response(text: str) -> bool:
    normalized = str(text or "")
    if not normalized.strip():
        return False
    marker_hits = sum(1 for marker in _PM_PROMPT_ECHO_MARKERS if marker in normalized)
    lower = normalized.lower()
    schema_echo = '"tasks"' in normalized and (
        '"title": "任务标题"' in normalized or '"title":"任务标题"' in normalized or '"title": "task title"' in lower
    )
    return "failed to parse action" in lower or (schema_echo and marker_hits >= 2)


def _pm_is_placeholder_task_title(value: Any) -> bool:
    text = _pm_strip_markdown_title_noise(value)
    if not text:
        return True
    normalized = re.sub(r"\s+", " ", text).strip()
    without_task_label = _pm_strip_task_label_prefix(normalized)
    if not without_task_label and _PM_TASK_LABEL_PREFIX.match(normalized):
        return True
    lower = normalized.lower()
    if lower in _PM_SCHEMA_PLACEHOLDER_VALUES:
        return True
    return bool(
        re.fullmatch(r"(?:task|任务|pm[-_ ]*task)\s*[-_#]*\s*\d+", lower, flags=re.IGNORECASE)
        or re.fullmatch(r"task[-_ ]*\d+", lower, flags=re.IGNORECASE)
    )


def _pm_strip_action_prefix(value: str) -> str:
    text = _pm_text(value)
    for marker in ("实现", "构建", "设计", "编写", "定义"):
        if text.startswith(marker) and len(text) > len(marker) + 1:
            return text[len(marker) :].strip()
    text = re.sub(r"^(?:implement|build|create|design|define|develop)\s+", "", text, flags=re.IGNORECASE)
    return text.strip()


def _pm_extract_requirement_subject(directive: str) -> str:
    text = str(directive or "")
    if not text.strip():
        return ""

    title_match = re.search(
        r"#\s*(?:Product\s+Requirements|需求|需求文档)\s*[—\-:：]\s*(.+?)\s*(?:\n|$)",
        text,
        flags=re.IGNORECASE,
    )
    if title_match:
        candidate = _pm_strip_action_prefix(str(title_match.group(1) or ""))
        if candidate and not _pm_is_placeholder_task_title(candidate):
            return candidate[:80]

    goal_match = re.search(
        r"(?:^|\n)##\s*Goal\s*\n(?P<section>.*?)(?=\n##\s|\Z)", text, flags=re.IGNORECASE | re.DOTALL
    )
    if goal_match:
        section = str(goal_match.group("section") or "")
        for raw_line in section.splitlines():
            line = re.sub(r"^\s*[-*]\s*", "", raw_line).strip()
            if not line:
                continue
            candidate = re.split(r"[:：。.;；]", line, maxsplit=1)[0].strip()
            candidate = _pm_strip_action_prefix(candidate)
            if candidate and not _pm_is_placeholder_task_title(candidate):
                return candidate[:80]

    chinese_goal = re.search(r"(?:实现|构建|编写|开发)([\u4e00-\u9fffA-Za-z0-9_ -]{4,80}?)(?:[:：。.;；\n]|$)", text)
    if chinese_goal:
        candidate = _pm_strip_action_prefix(str(chinese_goal.group(1) or ""))
        if candidate and not _pm_is_placeholder_task_title(candidate):
            return candidate[:80]
    return ""


def _pm_path_token_from_subject(subject: str) -> str:
    text = _pm_text(subject).lower()
    ascii_tokens = [token for token in re.findall(r"[a-z][a-z0-9_-]{2,}", text) if token not in _STOPWORDS]
    if ascii_tokens:
        return ascii_tokens[0]
    return "product"


def _pm_is_concrete_target_file_path(path: str) -> bool:
    token = str(path or "").strip().strip("'\"").replace("\\", "/").rstrip("/")
    if not token:
        return False
    leaf = token.rsplit("/", 1)[-1]
    return leaf in _PM_SCOPE_PATH_FILENAMES or Path(leaf).suffix.lower() in _PM_SCOPE_PATH_SUFFIXES


def _pm_split_concrete_targets_and_scopes(paths: list[str]) -> tuple[list[str], list[str]]:
    target_files: list[str] = []
    scope_paths: list[str] = []
    for path in paths:
        token = str(path or "").strip()
        if not token:
            continue
        if _pm_is_concrete_target_file_path(token):
            if token not in target_files:
                target_files.append(token)
            continue
        if token not in scope_paths:
            scope_paths.append(token)
    return target_files, scope_paths


def _pm_normalize_explicit_file_path(value: str) -> str:
    token = str(value or "").strip().strip("`'\"")
    token = token.replace("\\", "/").lstrip("./")
    if not token or token.startswith("/") or ".." in token.split("/"):
        return ""
    if not _pm_is_concrete_target_file_path(token):
        return ""
    if token.lower() == "readme.md":
        return "README.md"
    return token


def _pm_extract_concrete_file_paths_from_text(text: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for match in _PM_EXPLICIT_FILE_PATH_RE.finditer(str(text or "")):
        token = _pm_normalize_explicit_file_path(str(match.group(1) or ""))
        key = token.lower()
        if token and key not in seen:
            paths.append(token)
            seen.add(key)
    return paths


def _pm_append_unique_path(paths: list[str], path: str) -> None:
    token = str(path or "").strip()
    if not token:
        return
    lowered = {item.lower() for item in paths}
    if token.lower() not in lowered:
        paths.append(token)


def _pm_root_source_filename_from_text(text: str) -> str:
    source = str(text or "")
    lower = source.lower()

    if "calculator" in lower or "计算器" in source:
        return "calculator.py"
    if "guess" in lower and "number" in lower:
        return "guess_number.py"

    for match in _PM_BARE_FILENAME_HINT_RE.finditer(source):
        stem = str(match.group(1) or "").strip().lower()
        if not stem or stem in _STOPWORDS:
            continue
        if stem in {"readme", "pytest", "npm", "test", "tests", "python", "cli", "todo", "fixme", "stub"}:
            continue
        if stem in {"calculator", "calc"}:
            return "calculator.py"
        if stem in {"main", "app", "cli"}:
            return f"{stem}.py"

    if _PM_PYTHON_HINT_RE.search(source):
        return "main.py"
    return ""


def _pm_root_workspace_target_files_from_context(
    *,
    title: str,
    goal: str,
    description: str,
    directive: str,
) -> list[str]:
    combined = "\n".join(item for item in (title, goal, description, directive) if str(item or "").strip())
    if not _PM_ROOT_WORKSPACE_HINT_RE.search(combined):
        return []

    task_text = "\n".join(item for item in (title, goal, description) if str(item or "").strip())
    targets: list[str] = []
    source_context = task_text or combined
    for explicit_target in _pm_extract_concrete_file_paths_from_text(source_context):
        _pm_append_unique_path(targets, explicit_target)

    if _PM_SOURCE_FILE_HINT_RE.search(source_context):
        source_file = _pm_root_source_filename_from_text(source_context + "\n" + directive)
        if source_file:
            _pm_append_unique_path(targets, source_file)

    if _PM_README_HINT_RE.search(source_context):
        _pm_append_unique_path(targets, "README.md")

    return targets


def _pm_target_files_include_tests(target_files: list[str]) -> bool:
    for path in target_files:
        lowered = str(path or "").replace("\\", "/").lower()
        if not lowered:
            continue
        if lowered.startswith("tests/") or "/tests/" in lowered:
            return True
        filename = lowered.rsplit("/", 1)[-1]
        if filename.startswith("test_") or ".test." in filename or filename.endswith("_test.py"):
            return True
    return False


def _pm_infer_test_target_file_for_contract(
    *,
    title: str,
    goal: str,
    description: str,
    steps: list[str],
    acceptance: list[str],
    phase: str,
    target_files: list[str],
    directive: str,
) -> str:
    if _pm_target_files_include_tests(target_files):
        return ""

    del steps, acceptance
    explicit_hint_text = "\n".join([title, goal, description, phase])
    phase_token = str(phase or "").strip().lower()
    if phase_token not in {"verification", "validation", "verify", "qa", "test", "testing"} and not (
        _PM_TEST_CONTRACT_HINT_RE.search(explicit_hint_text)
    ):
        return ""

    source_candidates = [
        path
        for path in target_files
        if path.lower().endswith((".py", ".js", ".jsx", ".ts", ".tsx")) and not _pm_target_files_include_tests([path])
    ]
    source_file = (
        source_candidates[0]
        if source_candidates
        else _pm_root_source_filename_from_text("\n".join([title, goal, description, directive]))
    )
    if source_file:
        source_leaf = source_file.rsplit("/", 1)[-1]
        stem = Path(source_leaf).stem
        suffix = Path(source_leaf).suffix.lower()
        if suffix == ".py":
            return f"tests/test_{stem}.py"
        if suffix in {".ts", ".tsx"}:
            return f"tests/{stem}.test.ts"
        if suffix in {".js", ".jsx", ".mjs"}:
            return f"tests/{stem}.test.js"

    domain = _pm_path_token_from_subject(_pm_extract_requirement_subject(directive) or title)
    return f"tests/test_{domain}.py"


def _pm_root_workspace_contract_targets_from_directive(directive: str) -> tuple[str, str, str] | None:
    text = str(directive or "")
    if not _PM_ROOT_WORKSPACE_HINT_RE.search(text):
        return None
    source_file = _pm_root_source_filename_from_text(text)
    if not source_file:
        return None
    test_file = _pm_infer_test_target_file_for_contract(
        title="verification",
        goal="verification",
        description="verification",
        steps=[],
        acceptance=[],
        phase="verification",
        target_files=[source_file],
        directive=text,
    )
    readme_file = "README.md" if _PM_README_HINT_RE.search(text) else ""
    return source_file, test_file, readme_file
