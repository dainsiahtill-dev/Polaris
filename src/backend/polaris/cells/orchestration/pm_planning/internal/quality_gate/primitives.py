"""Domain-agnostic PM quality-gate primitives.

Dependency-free foundation for the PM task quality gate: text/path
normalization, scope-path classification, workspace-relative coercion,
prompt-leak / measurable-acceptance detection, dependency-reference
normalization, vendored-asset stripping, single-file UI steering,
deterministic-scaffold residue cleanup, and shared evidence-path helpers.

These helpers carry NO CLAUDE.md §8 game/card3d project-specific behavior.
This module is imported by :mod:`domain_contracts` and :mod:`gate`; it imports
neither, keeping the package an acyclic ``primitives -> domain_contracts ->
gate`` DAG. Bodies are moved verbatim from the historical ``task_quality_gate``
module to preserve behavior exactly (lossless decomposition).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from polaris.kernelone.quality import scan_workspace_artifact_quality

_PM_PROMPT_LEAK_TOKENS = (
    "you are ",
    "角色设定",
    "system prompt",
    "no yapping",
    "<thinking>",
    "<tool_call>",
)
_PM_CHINESE_PROMPT_LEAK_TOKENS = (
    "系统提示词",
    "开发者提示词",
    "角色提示词",
    "内部提示词",
    "完整提示词",
    "提示词泄露",
    "提示词泄漏",
    "提示词注入",
)
_CJK_CHAR_RE = re.compile(r"[\u3400-\u9fff]")
_PM_ACTION_TOKENS = (
    "add",
    "build",
    "connect",
    "deliver",
    "implement",
    "define",
    "design",
    "extract",
    "fix",
    "harden",
    "integrate",
    "persist",
    "write",
    "create",
    "refactor",
    "test",
    "update",
    "validate",
    "verify",
    "补充",
    "补齐",
    "持久化",
    "抽取",
    "记录",
    "接入",
    "落地",
    "收敛",
    "输出",
    "完成",
    "新增",
    "修复",
    "创建",
    "统一",
    "校验",
    "构建",
    "实现",
    "设计",
    "编写",
    "重构",
    "验证",
)
_PM_MEASURABLE_COMMAND_RE = re.compile(
    r"\b(curl|wget|httpie|npm|pnpm|yarn|npx|node|python|pytest|go\s+test|mvn|gradle|dotnet|cargo|grep|jq|awk|sed|powershell|pwsh)\b",
    re.IGNORECASE,
)
_PM_MEASURABLE_ASSERT_RE = re.compile(
    r"\b(verify|assert|expect|should|must|returns?|response|status|校验|验证|断言|应当|必须)\b",
    re.IGNORECASE,
)
_PM_MEASURABLE_RESULT_RE = re.compile(
    r"\b(200|201|202|204|400|401|403|404|409|422|500|pass|fail|true|false|ok|error)\b|[<>]=?\s*\d+|\b\d+\s*(ms|s|sec|seconds?|分钟|小时|days?)\b",
    re.IGNORECASE,
)
_PM_MEASURABLE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|[\w.\-]+[\\/][\w.\-/\\]+)",
)
_PM_FILE_EVIDENCE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|[\w.\-]+[\\/][\w.\-/\\]+\.[A-Za-z0-9]+)",
)
_PM_MEASURABLE_BACKTICK_RE = re.compile(r"`[^`]{2,}`")
_DETERMINISTIC_SCAFFOLD_MARKER_ERROR_RE = re.compile(
    r"deterministic scaffold marker .+ in (?P<path>.+)$",
    re.IGNORECASE,
)
_PM_EXECUTABLE_BACKTICK_RE = re.compile(r"`([^`]{2,})`")
_PM_PLACEHOLDER_ACCEPTANCE_RE = re.compile(
    r"("
    r"占位\s*(?:输出|脚本|检查|测试|即可|可以)"
    r"|(?:可|可以|允许)[^\n。；;]*(?:占位|placeholder|stub|dummy)"
    r"|(?:最小|空|empty)[\w\s-]*(?:占位|placeholder|stub|dummy)"
    r"|(?:placeholder|stub|dummy)[\w\s-]*(?:output|script|only|suffices?|is\s+ok)"
    r"|manifest[\w\s-]*only"
    r"|只检查\s*(?:manifest|package\.json)"
    r"|仅检查\s*(?:manifest|package\.json)"
    r"|(?:only|just|merely)\s+(?:checks?|parses?|reads?)\s+(?:the\s+)?(?:manifest|package\.json)"
    r")",
    re.IGNORECASE,
)
_PM_PLACEHOLDER_CLEANUP_ACCEPTANCE_RE = re.compile(
    r"\b(?:replace|remove|removing|replacing)\b[^\n。；;]*(?:placeholder|stub|dummy)",
    re.IGNORECASE,
)
_PM_SCOPE_ROOTS = {
    "app",
    "backend",
    "components",
    "docs",
    "electron",
    "frontend",
    "lib",
    "packages",
    "scripts",
    "services",
    "src",
    "tests",
    "workspace",
}
_PM_SCOPE_FILENAMES = {
    "CMakeLists.txt",
    "cmakelists.txt",
    "package.json",
    "README.md",
    "tsconfig.json",
    "vite.config.ts",
    "vitest.config.ts",
    "tailwind.config.js",
    "postcss.config.js",
    "pyproject.toml",
}
_PM_SCOPE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".css",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".html",
    ".hxx",
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
_PM_NON_PATH_TEXT_RE = re.compile(r"[\s,，、；;：:。]|[\u4e00-\u9fff]")


def _strip_wrapping_quotes(token: str) -> str:
    text = str(token or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _normalize_path_list(value: Any) -> list[str]:
    if isinstance(value, str):
        entries = [segment.strip() for segment in value.split(",") if segment.strip()]
    elif isinstance(value, list):
        entries = [str(item).strip() for item in value if str(item).strip()]
    else:
        entries = []
    normalized: list[str] = []
    for item in entries:
        token = str(item).strip().replace("\\", "/")
        while token.startswith("./"):
            token = token[2:]
        token = re.sub(r"/+", "/", token)
        if token:
            normalized.append(token)
    return normalized


def _normalize_dep_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = _normalize_text(item)
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _task_id_at_position(tasks: list[dict[str, Any]], position: int) -> str:
    if position <= 0 or position > len(tasks):
        return ""
    return _normalize_text(tasks[position - 1].get("id"))


def _resolve_dependency_ref(token: str, tasks: list[dict[str, Any]], known_ids: set[str]) -> str:
    if token in known_ids:
        return token
    parts = token.split("-")
    if len(parts) == 2 and parts[0].upper() == "PM" and parts[1].isdigit():
        mapped = _task_id_at_position(tasks, int(parts[1]))
        if mapped:
            return mapped
    if len(parts) >= 3 and parts[0].upper() == "PM" and parts[-1].isdigit():
        mapped = _task_id_at_position(tasks, int(parts[-1]))
        if mapped:
            return mapped
    return token


def _normalize_dependency_refs_in_place(tasks: list[dict[str, Any]]) -> int:
    known_ids = {_normalize_text(task.get("id")) for task in tasks if _normalize_text(task.get("id"))}
    normalized_count = 0
    for task in tasks:
        task_id = _normalize_text(task.get("id"))
        raw_deps = task.get("depends_on")
        target_key = "depends_on"
        if not isinstance(raw_deps, list):
            raw_deps = task.get("dependencies")
            target_key = "dependencies"
        deps = _normalize_dep_list(raw_deps)
        if not deps:
            continue
        rewritten: list[str] = []
        seen: set[str] = set()
        for dep in deps:
            resolved = _resolve_dependency_ref(dep, tasks, known_ids)
            if not resolved or resolved == task_id or resolved in seen:
                if resolved != dep:
                    normalized_count += 1
                continue
            seen.add(resolved)
            rewritten.append(resolved)
            if resolved != dep:
                normalized_count += 1
        task[target_key] = rewritten
    return normalized_count


def _unknown_dependency_refs(tasks: list[dict[str, Any]]) -> list[str]:
    known_ids = {_normalize_text(task.get("id")) for task in tasks if _normalize_text(task.get("id"))}
    unknown: list[str] = []
    for task in tasks:
        task_id = _normalize_text(task.get("id"))
        deps = task.get("depends_on")
        if not isinstance(deps, list):
            deps = task.get("dependencies")
        for dep in _normalize_dep_list(deps):
            if dep not in known_ids:
                unknown.append(f"{task_id}: unknown dependency `{dep}`")
    return unknown


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_path(value: Any) -> str:
    token = str(value or "").strip().replace("\\", "/")
    token = re.sub(r"^[A-Za-z]:/", "", token)
    while token.startswith("./"):
        token = token[2:]
    token = token.strip("/")
    token = re.sub(r"/+", "/", token)
    return token


def _canonical_pm_contract_output_path(value: str) -> str:
    parts = [part for part in str(value or "").split("/") if part]
    if not parts:
        return ""
    if parts[-1].lower() == "readme.md":
        parts[-1] = "README.md"
    return "/".join(parts)


def _domain_path_roots(domain: str, scope_paths: dict[str, str]) -> set[str]:
    if domain == "tests":
        return {"tests"}
    primary = _normalize_path(scope_paths.get(domain, f"src/{domain}/index.ts"))
    parent = os.path.dirname(primary).replace("\\", "/").strip("/") if primary else ""
    roots = {parent or f"src/{domain}"}
    if primary:
        roots.add(primary)
        if parent:
            roots.add(parent)
    return {root for root in roots if root}


def _is_concrete_pm_scope_path(value: Any) -> bool:
    raw = _strip_wrapping_quotes(str(value or "").strip()).replace("\\", "/")
    if not raw:
        return False
    if raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", raw):
        return False
    if _PM_NON_PATH_TEXT_RE.search(raw):
        return False

    token = raw
    while token.startswith("./"):
        token = token[2:]
    token = re.sub(r"/+", "/", token.strip("/"))
    if not token or token in {".", "*", "**"}:
        return False
    parts = [part for part in token.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        return False

    filename = parts[-1]
    if token in _PM_SCOPE_FILENAMES or filename in _PM_SCOPE_FILENAMES:
        return True
    if os.path.splitext(filename)[1].lower() in _PM_SCOPE_SUFFIXES:
        return True
    if parts[0] in _PM_SCOPE_ROOTS and (len(parts) > 1 or raw.endswith("/")):
        return True
    return token.rstrip("/") in _PM_SCOPE_ROOTS


def _is_file_like_pm_scope_path(value: Any) -> bool:
    raw = _strip_wrapping_quotes(str(value or "").strip()).replace("\\", "/")
    if not raw:
        return False
    token = raw
    while token.startswith("./"):
        token = token[2:]
    token = re.sub(r"/+", "/", token.strip("/"))
    if not token or token in {".", "*", "**"}:
        return False
    parts = [part for part in token.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        return False
    filename = parts[-1]
    return (
        token in _PM_SCOPE_FILENAMES
        or filename in _PM_SCOPE_FILENAMES
        or os.path.splitext(filename)[1].lower() in _PM_SCOPE_SUFFIXES
    )


def _workspace_prefix(workspace_full: Any) -> str:
    token = str(workspace_full or "").strip()
    if not token:
        return ""
    try:
        return os.path.normcase(os.path.abspath(token))
    except (OSError, ValueError):
        return ""


def _path_parts_contain_parent(candidate: str) -> bool:
    token = candidate.replace("\\", "/")
    return any(part == ".." for part in token.split("/"))


def _workspace_relative_path(candidate: Any, workspace_full: Any) -> str:
    raw = _strip_wrapping_quotes(str(candidate or "").strip())
    if not raw or raw.startswith("~") or _path_parts_contain_parent(raw):
        return ""

    workspace_prefix = _workspace_prefix(workspace_full)
    if not workspace_prefix:
        if os.path.isabs(raw):
            return ""
        relative = raw.replace("\\", "/")
        while relative.startswith("./"):
            relative = relative[2:]
        return re.sub(r"/+", "/", relative.strip("/"))

    try:
        resolved = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(workspace_prefix, raw))
        common = os.path.commonpath([workspace_prefix, os.path.normcase(resolved)])
    except (OSError, ValueError):
        return ""
    if os.path.normcase(common) != workspace_prefix:
        return ""
    try:
        relative = os.path.relpath(resolved, workspace_prefix)
    except (OSError, ValueError):
        return ""
    if relative == ".":
        return ""
    return re.sub(r"/+", "/", relative.replace("\\", "/").strip("/"))


def _is_workspace_bound_concrete_path(candidate: Any, workspace_full: Any) -> bool:
    relative = _workspace_relative_path(candidate, workspace_full)
    if not relative:
        return False
    return _is_concrete_pm_scope_path(relative)


def _is_directory_scope_evidenced(candidate: Any, concrete_paths: list[str], workspace_full: Any) -> bool:
    """Accept a bare directory token when evidence shows it is a real dir.

    ``_PM_SCOPE_ROOTS`` is a fixed convention list (src/app/docs/...), so any
    legitimate project layout outside it — ``vendor``, ``assets``, ``public`` —
    used to be rejected as "outside workspace" (live factory-bench L2-10:
    scope ``vendor`` next to target ``vendor/marked.min.js`` failed planning
    three times and skipped the Director). Evidence beats enumeration: the
    token counts as a directory scope when a sibling concrete path in the SAME
    task lives under it, or the directory already exists in the workspace.
    """
    raw = _strip_wrapping_quotes(str(candidate or "").strip()).replace("\\", "/")
    if not raw or raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", raw):
        return False
    if _PM_NON_PATH_TEXT_RE.search(raw):
        return False
    token = raw
    while token.startswith("./"):
        token = token[2:]
    token = re.sub(r"/+", "/", token.strip("/"))
    if not token or any(part in {".", "..", "*", "**"} for part in token.split("/")):
        return False
    prefix_folded = f"{token.casefold()}/"
    for path in concrete_paths:
        normalized = str(path or "").replace("\\", "/").casefold()
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized.startswith(prefix_folded):
            return True
    workspace = str(workspace_full or "").strip()
    if workspace:
        try:
            if (Path(workspace) / token).is_dir():
                return True
        except OSError:
            return False
    return False


def _coerce_pm_path_to_workspace_relative(candidate: Any, workspace_full: Any) -> tuple[str, str]:
    """Coerce an LLM-provided PM path into a workspace-relative path.

    PM contracts are executable handoffs. Absolute paths are not acceptable in
    those contracts because Director write gates are scoped to the active
    workspace. If a path is already inside the workspace, keep its true relative
    form. If an LLM names a throwaway external project root such as
    ``C:/Temp/roguelike-ts/src/app.ts``, preserve only the project-internal
    suffix (``src/app.ts``). Parent traversal and non-concrete path text still
    fail closed.
    """
    raw = _strip_wrapping_quotes(str(candidate or "").strip())
    if not raw or raw.startswith("~") or _path_parts_contain_parent(raw):
        return "", ""

    relative = _workspace_relative_path(raw, workspace_full)
    if relative and _is_concrete_pm_scope_path(relative):
        return _canonical_pm_contract_output_path(relative), ""

    normalized_raw = raw.replace("\\", "/")
    is_absolute = os.path.isabs(raw) or bool(re.match(r"^[A-Za-z]:[\\/]", raw)) or normalized_raw.startswith("/")
    if not is_absolute:
        token = _normalize_path(raw)
        output_token = _canonical_pm_contract_output_path(token)
        return (output_token, "") if output_token and _is_concrete_pm_scope_path(token) else ("", "")

    without_drive = re.sub(r"^[A-Za-z]:/", "", normalized_raw).strip("/")
    parts = [part for part in without_drive.split("/") if part]
    if not parts:
        return "", ""

    suffix_candidates: list[tuple[list[str], str]] = []
    for index, part in enumerate(parts):
        if part.lower() in _PM_SCOPE_ROOTS:
            suffix_candidates.append((parts[index:], "/".join(parts[:index])))
            break

    filename = parts[-1]
    if filename in _PM_SCOPE_FILENAMES or os.path.splitext(filename)[1].lower() in _PM_SCOPE_SUFFIXES:
        if len(parts) >= 3 and parts[0].lower() in {"temp", "tmp"}:
            suffix_candidates.append((parts[2:], "/".join(parts[:2])))
        if len(parts) >= 2:
            suffix_candidates.append((parts[-2:], "/".join(parts[:-2])))
        suffix_candidates.append(([filename], "/".join(parts[:-1])))

    for suffix_parts, stripped_root in suffix_candidates:
        token = _normalize_path("/".join(suffix_parts))
        if token and _is_concrete_pm_scope_path(token):
            suffix_text = "/".join(suffix_parts).strip("/")
            original_root = stripped_root
            if suffix_text and normalized_raw.lower().endswith("/" + suffix_text.lower()):
                original_root = normalized_raw[: -len(suffix_text)].rstrip("/")
            return _canonical_pm_contract_output_path(token), original_root
    return "", ""


def _dedupe_paths(paths: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for path in paths:
        token = str(path or "").strip()
        key = token.lower()
        if not token or key in seen:
            continue
        seen.add(key)
        deduped.append(token)
    return deduped


def _derive_scope_from_pm_targets(target_files: list[str]) -> list[str]:
    scopes: list[str] = []
    for target in target_files:
        token = _normalize_path(target)
        if not token:
            continue
        directory = os.path.dirname(token).replace("\\", "/").strip("/")
        scope = directory or token
        if scope and scope not in scopes:
            scopes.append(scope)
    return scopes


def _replace_external_roots_in_text(value: Any, stripped_roots: set[str]) -> Any:
    if not stripped_roots:
        return value
    if isinstance(value, str):
        text = value
        for root in sorted((item for item in stripped_roots if item), key=len, reverse=True):
            root_slash = root.replace("\\", "/").strip("/")
            if not root_slash:
                continue
            drive_variants = [root_slash]
            if re.match(r"^[A-Za-z]:/", root_slash):
                drive_variants.append(root_slash.replace("/", "\\"))
            for variant in drive_variants:
                text = text.replace(variant + "/", "")
                text = text.replace(variant + "\\", "")
                text = text.replace(variant, "workspace root")
        return text
    if isinstance(value, list):
        return [_replace_external_roots_in_text(item, stripped_roots) for item in value]
    if isinstance(value, dict):
        return {key: _replace_external_roots_in_text(item, stripped_roots) for key, item in value.items()}
    return value


_VENDORED_MINIFIED_ASSET_RE = re.compile(r"(?:^|/)[\w.-]+\.min\.(?:js|css)$", re.IGNORECASE)


def _strip_unfulfillable_vendored_targets_in_place(tasks: list[dict[str, Any]]) -> int:
    """Remove third-party minified asset paths from write-target fields.

    An offline Director cannot materialize a real vendored library — the only
    possible "fulfillment" of a ``lib/marked.min.js`` target is a hallucinated
    fake, and declared-target verification then fails the whole task even when
    the delivered product is a working self-contained implementation (live
    factory-bench L2-10 r6: QA passed the workspace, the task died on the
    missing vendored file). Strips such targets, drops acceptance lines that
    re-demand them, and steers the contract toward a self-contained build.
    """
    changed = 0
    note = "不要落盘第三方压缩库文件；改为自包含实现(内联/手写解析)或运行时 CDN 引用。"
    for task in tasks:
        stripped: list[str] = []
        for field in ("target_files", "scope_paths", "context_files"):
            original = [str(item) for item in (task.get(field) or []) if str(item).strip()]
            kept = [item for item in original if not _VENDORED_MINIFIED_ASSET_RE.search(item.replace("\\", "/"))]
            if len(kept) != len(original):
                stripped.extend(item for item in original if item not in kept)
                task[field] = kept
        if not stripped:
            continue
        changed += 1
        unique_assets = sorted(set(stripped))
        description = str(task.get("description") or "").strip()
        if note not in description:
            task["description"] = (
                description
                + ("\n" if description else "")
                + f"[quality-gate] 已移除不可离线物化的第三方资产目标: {', '.join(unique_assets[:4])}。{note}"
            )
        for acc_field in ("acceptance", "acceptance_criteria"):
            rows = task.get(acc_field)
            if isinstance(rows, list):
                filtered = [row for row in rows if not any(asset in str(row) for asset in unique_assets)]
                if len(filtered) != len(rows):
                    task[acc_field] = filtered
    return changed


_INTERACTIVE_APP_HINT_RE = re.compile(r"实时|交互|游戏|动态|单文件|interactive|game|realtime|app\b", re.IGNORECASE)


def _steer_single_file_ui_tasks_in_place(tasks: list[dict[str, Any]]) -> int:
    """Split single-HTML interactive-app tasks into a modular file contract.

    Output-budget physics: a local Director cannot emit a complete >6-7KB
    single file inside its output ceiling — every whole-file write truncates
    at the same place, and a ~10K-token mega-write costs ~9 minutes of wall
    clock before failing (live factory-bench L2-11 r6/r7, where the PM even
    declared "单文件打字测试器"). Steering the contract to index.html +
    style.css + app.js keeps every write small enough to converge.
    """
    changed = 0
    for task in tasks:
        targets = [str(item).strip() for item in (task.get("target_files") or []) if str(item).strip()]
        html_targets = [item for item in targets if item.lower().endswith((".html", ".htm"))]
        code_targets = [item for item in targets if item.lower().endswith((".js", ".css", ".py", ".ts"))]
        if len(html_targets) != 1 or code_targets:
            continue
        text_blob = " ".join(str(task.get(key) or "") for key in ("title", "goal", "description"))
        if not _INTERACTIVE_APP_HINT_RE.search(text_blob):
            continue
        task["target_files"] = [*targets, "style.css", "app.js"]
        scope = [str(item).strip() for item in (task.get("scope_paths") or []) if str(item).strip()]
        for extra in ("style.css", "app.js"):
            if extra not in scope:
                scope.append(extra)
        task["scope_paths"] = scope
        note = (
            "[quality-gate] 禁止单文件大产物：HTML 只保留结构，样式写入 style.css、"
            "逻辑写入 app.js（每个文件 ≤150 行）。单文件大写入会被输出预算截断且无法收敛。"
        )
        description = str(task.get("description") or "").strip()
        if note not in description:
            task["description"] = description + ("\n" if description else "") + note
        changed += 1
    return changed


def _sanitize_pm_task_paths_in_place(tasks: list[dict[str, Any]], workspace_full: str) -> int:
    normalized_count = 0
    for task in tasks:
        stripped_roots: set[str] = set()
        for field in ("context_files", "target_files", "scope_paths"):
            original_paths = _normalize_path_list(task.get(field) or [])
            sanitized: list[str] = []
            for raw_path in original_paths:
                relative, stripped_root = _coerce_pm_path_to_workspace_relative(raw_path, workspace_full)
                if not relative:
                    continue
                sanitized.append(relative)
                if stripped_root:
                    stripped_roots.add(stripped_root)
            sanitized = _dedupe_paths(sanitized)
            if sanitized != original_paths:
                normalized_count += 1
            task[field] = sanitized

        if not task.get("scope_paths") and task.get("target_files"):
            task["scope_paths"] = _derive_scope_from_pm_targets(
                [str(item) for item in task.get("target_files") or [] if str(item).strip()]
            )
            normalized_count += 1

        if stripped_roots:
            for field in (
                "title",
                "goal",
                "description",
                "backlog_ref",
                "acceptance",
                "acceptance_criteria",
                "execution_checklist",
                "steps",
            ):
                if field in task:
                    task[field] = _replace_external_roots_in_text(task.get(field), stripped_roots)
    return normalized_count


def _drop_unknown_dependency_refs_in_place(tasks: list[dict[str, Any]]) -> int:
    known_ids = {_normalize_text(task.get("id")) for task in tasks if _normalize_text(task.get("id"))}
    normalized_count = 0
    for task in tasks:
        task_id = _normalize_text(task.get("id"))
        target_key = "depends_on"
        raw_deps = task.get(target_key)
        if not isinstance(raw_deps, list):
            target_key = "dependencies"
            raw_deps = task.get(target_key)
        deps = _normalize_dep_list(raw_deps)
        if not deps:
            continue
        kept: list[str] = []
        seen: set[str] = set()
        for dep in deps:
            if dep not in known_ids or dep == task_id or dep in seen:
                normalized_count += 1
                continue
            seen.add(dep)
            kept.append(dep)
        if kept != deps:
            task[target_key] = kept
    return normalized_count


def _collect_task_scope_paths(task: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    paths.extend(_normalize_path_list(task.get("scope_paths") or []))
    paths.extend(_normalize_path_list(task.get("target_files") or []))
    paths.extend(_normalize_path_list(task.get("context_files") or []))
    return paths


def _collect_task_delivery_paths(task: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    paths.extend(_normalize_path_list(task.get("scope_paths") or []))
    paths.extend(_normalize_path_list(task.get("target_files") or []))
    return paths


def _last_task_id(tasks: list[dict[str, Any]]) -> str:
    for task in reversed(tasks):
        task_id = _normalize_text(task.get("id"))
        if task_id:
            return task_id
    return ""


def _unique_task_id(existing_ids: set[str], base: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_-]+", "-", base.strip()).strip("-").upper()
    if not token:
        token = "PM-AUTO-TASK"
    candidate = token
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{token}-{suffix}"
        suffix += 1
    existing_ids.add(candidate)
    return candidate


def _append_unique_text_item(task: dict[str, Any], field: str, item: str) -> int:
    existing = task.get(field)
    if not isinstance(existing, list):
        task[field] = [item]
        return 1
    normalized_existing = {_normalize_text(value).lower() for value in existing if _normalize_text(value)}
    normalized_item = _normalize_text(item).lower()
    if normalized_item and normalized_item not in normalized_existing:
        existing.append(item)
        return 1
    return 0


def _contains_prompt_leakage(text: str) -> bool:
    lowered = _normalize_text(text).lower()
    if not lowered:
        return False
    if any(token in lowered for token in _PM_PROMPT_LEAK_TOKENS):
        return True
    return any(token in lowered for token in _PM_CHINESE_PROMPT_LEAK_TOKENS)


def _title_is_too_short(title: str) -> bool:
    if not title:
        return True
    cjk_count = len(_CJK_CHAR_RE.findall(title))
    if cjk_count >= 5:
        return False
    return len(title) < 10


def _has_measurable_acceptance_anchor(acceptance_items: list[str]) -> bool:
    for item in acceptance_items:
        normalized = _normalize_text(item)
        if not normalized:
            continue
        if _PM_MEASURABLE_BACKTICK_RE.search(normalized):
            return True
        if _PM_MEASURABLE_COMMAND_RE.search(normalized):
            return True
        has_assert = bool(_PM_MEASURABLE_ASSERT_RE.search(normalized))
        has_observable = bool(_PM_MEASURABLE_RESULT_RE.search(normalized) or _PM_MEASURABLE_PATH_RE.search(normalized))
        if has_assert and has_observable:
            return True
    return False


def _has_executable_or_file_acceptance_anchor(acceptance_items: list[str]) -> bool:
    """Return True when acceptance proves an executable check or concrete file evidence.

    ``_has_measurable_acceptance_anchor`` intentionally accepts observable outcomes
    such as HTTP status codes. Director/ChiefEngineer handoff tasks need a stronger
    anchor so a PM contract cannot pass with generic "it works" style acceptance.
    """
    for item in acceptance_items:
        normalized = _normalize_text(item)
        if not normalized:
            continue
        if _PM_MEASURABLE_COMMAND_RE.search(normalized):
            return True
        for match in _PM_EXECUTABLE_BACKTICK_RE.finditer(normalized):
            if _PM_MEASURABLE_COMMAND_RE.search(match.group(1)):
                return True
        if _PM_MEASURABLE_ASSERT_RE.search(normalized) and _PM_FILE_EVIDENCE_PATH_RE.search(normalized):
            return True
    return False


def _has_placeholder_or_manifest_only_acceptance(acceptance_items: list[str]) -> bool:
    for item in acceptance_items:
        normalized = _normalize_text(item)
        if (
            normalized
            and not _PM_PLACEHOLDER_CLEANUP_ACCEPTANCE_RE.search(normalized)
            and _PM_PLACEHOLDER_ACCEPTANCE_RE.search(normalized)
        ):
            return True
    return False


def _dedupe_text_items(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = _normalize_text(item)
        key = token.lower()
        if not token or key in seen:
            continue
        seen.add(key)
        deduped.append(token)
    return deduped


def _normalize_artifact_quality_relative_path(value: Any) -> str:
    text = str(value or "").strip().strip("'\"").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if not text or text.startswith("../") or text.startswith("/"):
        return ""
    if any(ch in text for ch in ("*", "?")):
        return ""
    return text


def _deterministic_scaffold_residue_paths(workspace_full: str) -> list[str]:
    paths: list[str] = []
    for error in scan_workspace_artifact_quality(workspace_full):
        match = _DETERMINISTIC_SCAFFOLD_MARKER_ERROR_RE.search(str(error or ""))
        if not match:
            continue
        relative_path = _normalize_artifact_quality_relative_path(match.group("path"))
        if relative_path:
            paths.append(relative_path)
    return sorted(set(paths))


def _scope_paths_for_target_files(target_files: list[str]) -> list[str]:
    scope_paths: list[str] = []
    for relative_path in target_files:
        parent = str(Path(relative_path).parent).replace("\\", "/")
        scope_paths.append(relative_path if parent == "." else parent)
    return _dedupe_text_items(scope_paths)


def _append_deterministic_scaffold_residue_cleanup_task(
    normalized: dict[str, Any],
    normalized_tasks: list[dict[str, Any]],
    *,
    workspace_full: str,
    verify_command: str,
) -> int:
    if any(
        isinstance(task.get("metadata"), dict)
        and task["metadata"].get("autofix_reason") == "deterministic_scaffold_residue_cleanup"
        for task in normalized_tasks
    ):
        return 0

    residue_paths = _deterministic_scaffold_residue_paths(workspace_full)
    if not residue_paths:
        return 0

    existing_ids = {str(task.get("id") or "").strip() for task in normalized_tasks}
    task_id = "PM-AUTO-SEED-RESIDUE-CLEANUP"
    suffix = 2
    while task_id in existing_ids:
        task_id = f"PM-AUTO-SEED-RESIDUE-CLEANUP-{suffix}"
        suffix += 1

    previous_task_id = ""
    for task in reversed(normalized_tasks):
        previous_task_id = str(task.get("id") or "").strip()
        if previous_task_id:
            break

    normalized_tasks.append(
        {
            "id": task_id,
            "title": "Clean deterministic scaffold residue",
            "goal": ("Remove generated seed/scaffold markers from final workspace artifacts before integration QA."),
            "description": (
                "Rewrite declared residue files so production verification no longer depends on deterministic "
                "seed markers or placeholder verification strings."
            ),
            "phase": "verification",
            "assigned_to": "director",
            "depends_on": [previous_task_id] if previous_task_id else [],
            "target_files": residue_paths,
            "scope_paths": _scope_paths_for_target_files(residue_paths),
            "execution_checklist": [
                "Read each declared residue target file.",
                (
                    "Replace audit-seed, planning scenario, build verification completed, "
                    "test verification completed, and related deterministic scaffold markers."
                ),
                f"Run `{verify_command}` and confirm artifact quality passes.",
            ],
            "acceptance_criteria": [
                (
                    "Declared target files contain no audit-seed, planning scenario, build verification completed, "
                    "test verification completed, or deterministic scaffold markers."
                ),
                f"Run `{verify_command}` passes.",
            ],
            "metadata": {
                "autofix_reason": "deterministic_scaffold_residue_cleanup",
                "source": "artifact_quality_scan",
                "residue_count": len(residue_paths),
                "overall_goal": str(normalized.get("overall_goal") or "").strip(),
            },
        }
    )
    return 1


def _representative_workspace_file_for_scope(scope_path: Any, workspace_full: Any) -> str:
    relative = _workspace_relative_path(scope_path, workspace_full)
    if not relative:
        relative = _normalize_path(scope_path)
    if not relative:
        return ""
    if _is_file_like_pm_scope_path(relative):
        return relative

    workspace_prefix = _workspace_prefix(workspace_full)
    if not workspace_prefix:
        return ""
    try:
        scope_root = Path(workspace_prefix) / relative
    except (OSError, ValueError):
        return ""
    if not scope_root.is_dir():
        return ""

    skipped_dirs = {".git", ".polaris", "__pycache__", "build", "coverage", "dist", "node_modules"}
    try:
        for current_root, dirs, files in os.walk(scope_root):
            dirs[:] = sorted(
                directory for directory in dirs if directory not in skipped_dirs and not directory.startswith(".")
            )
            for filename in sorted(files):
                candidate = Path(current_root) / filename
                try:
                    relative_candidate = candidate.relative_to(workspace_prefix).as_posix()
                except ValueError:
                    continue
                if _is_file_like_pm_scope_path(relative_candidate):
                    return relative_candidate
    except OSError:
        return ""
    return ""


def _fallback_file_evidence_path_for_scope(scope_path: Any) -> str:
    normalized = _normalize_path(scope_path)
    if not normalized or not _is_concrete_pm_scope_path(normalized):
        return ""
    if _is_file_like_pm_scope_path(normalized):
        return normalized
    if normalized.startswith(("src/", "tests/")) or normalized in {"src", "tests"}:
        return f"{normalized.rstrip('/')}/index.ts"
    if normalized.startswith("docs/") or normalized == "docs":
        return f"{normalized.rstrip('/')}/README.md"
    if normalized.startswith("scripts/") or normalized == "scripts":
        return f"{normalized.rstrip('/')}/index.js"
    return f"{normalized.rstrip('/')}/README.md"
