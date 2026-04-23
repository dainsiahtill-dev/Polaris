"""Workspace status and plan template utilities for Polaris Loop."""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    from polaris.infrastructure.storage import LocalFileSystemAdapter
    from polaris.kernelone.fs import KernelFileSystem
except ImportError:  # pragma: no cover - script-mode fallback
    from polaris.infrastructure.storage import LocalFileSystemAdapter  # type: ignore
    from polaris.kernelone.fs import KernelFileSystem  # type: ignore


def _kernel_fs(workspace: str) -> KernelFileSystem:
    workspace_abs = os.path.abspath(workspace or os.getcwd())
    return KernelFileSystem(workspace_abs, LocalFileSystemAdapter())


def _needs_literal_newline_normalization(text: str) -> bool:
    """检查文本是否需要字面量换行规范化。"""
    if not isinstance(text, str):
        return False
    escaped_count = text.count("\\n")
    has_real_newline = "\n" in text
    return escaped_count >= 2 and not has_real_newline


def _normalize_literal_newlines(text: str) -> str:
    """规范化字面量换行。"""
    normalized = text
    normalized = normalized.replace("\\r\\n", "\r\n")
    normalized = normalized.replace("\\n", "\n")
    normalized = normalized.replace("\\t", "\t")
    return normalized


def _is_legacy_game_plan_template(text: str) -> bool:
    if not isinstance(text, str):
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    if not all(line.startswith("#") for line in lines):
        return False
    legacy_markers = (
        "MMO_CORE_SPEC.md",
        "apps/game-client/src/main.ts",
        "apps/physics-lab/src/main.ts",
    )
    hits = sum(1 for marker in legacy_markers if marker in text)
    return hits >= 2


def _is_chinese_profile(profile: str) -> bool:
    normalized = str(profile or "").strip().lower()
    if not normalized:
        return False
    return (
        normalized.endswith("_zh")
        or normalized.startswith("zh")
        or normalized in ("cn", "chinese")
        or "zhenguan" in normalized
    )


def _find_workspace_root(start: str) -> str:
    current = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(current, "docs")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return ""


def _infer_workspace_from_plan_path(path: str) -> str:
    base = os.path.abspath(os.path.dirname(path or os.getcwd()))
    docs_root = _find_workspace_root(base)
    if docs_root:
        return docs_root

    env_workspace = str(os.environ.get("KERNELONE_WORKSPACE") or "").strip()
    if env_workspace:
        env_workspace_abs = os.path.abspath(env_workspace)
        env_docs_root = _find_workspace_root(env_workspace_abs)
        if env_docs_root:
            return env_docs_root
        if os.path.isdir(env_workspace_abs):
            return env_workspace_abs

    cwd_docs_root = _find_workspace_root(os.getcwd())
    if cwd_docs_root:
        return cwd_docs_root

    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    metadata_dir = get_workspace_metadata_dir_name()
    current = base
    while True:
        # Check for current metadata dir (e.g. .polaris) or legacy .polaris
        basename = os.path.basename(current).lower()
        if basename in (metadata_dir, ".polaris"):
            parent = os.path.dirname(current)
            if parent and parent != current:
                return parent
        if os.path.isdir(os.path.join(current, metadata_dir)) or os.path.isdir(os.path.join(current, ".polaris")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return base


def _detect_project_traits(workspace: str) -> dict[str, Any]:
    traits: dict[str, Any] = {
        "python": False,
        "node": False,
        "go": False,
        "rust": False,
        "docs": False,
        "frontend": False,
        "backend": False,
        "package_manager": "",
        "app_dirs": [],
    }
    if not workspace:
        return traits

    def _has_file(rel_path: str) -> bool:
        return os.path.isfile(os.path.join(workspace, rel_path))

    def _has_dir(rel_path: str) -> bool:
        return os.path.isdir(os.path.join(workspace, rel_path))

    traits["python"] = _has_file("pyproject.toml") or _has_file("requirements.txt") or _has_file("setup.py")
    traits["node"] = _has_file("package.json")
    traits["go"] = _has_file("go.mod")
    traits["rust"] = _has_file("Cargo.toml")
    traits["docs"] = _has_dir("docs")

    traits["frontend"] = (
        _has_dir("src/frontend") or _has_dir("frontend") or _has_dir("apps/web") or _has_dir("apps/frontend")
    )
    traits["backend"] = (
        _has_dir("src/backend")
        or _has_dir("backend")
        or _has_dir("apps/server")
        or bool(traits["python"] or traits["go"] or traits["rust"])
    )

    if traits["node"]:
        if _has_file("pnpm-lock.yaml"):
            traits["package_manager"] = "pnpm"
        elif _has_file("yarn.lock"):
            traits["package_manager"] = "yarn"
        else:
            traits["package_manager"] = "npm"

    apps_root = os.path.join(workspace, "apps")
    if os.path.isdir(apps_root):
        app_dirs: list[str] = []
        try:
            for name in sorted(os.listdir(apps_root)):
                if os.path.isdir(os.path.join(apps_root, name)):
                    app_dirs.append(name)
        except (RuntimeError, ValueError):
            app_dirs = []
        traits["app_dirs"] = app_dirs

    return traits


def _project_summary(profile: str, traits: dict[str, Any]) -> str:
    zh = _is_chinese_profile(profile)
    labels: list[str] = []

    if traits.get("python"):
        labels.append("Python")
    if traits.get("node"):
        labels.append("Node.js/TypeScript")
    if traits.get("go"):
        labels.append("Go")
    if traits.get("rust"):
        labels.append("Rust")

    if traits.get("frontend") and traits.get("backend"):
        labels.append("前后端分层" if zh else "Frontend + Backend")
    elif traits.get("frontend"):
        labels.append("前端应用" if zh else "Frontend App")
    elif traits.get("backend"):
        labels.append("后端服务" if zh else "Backend Service")

    if traits.get("docs"):
        labels.append("文档驱动" if zh else "Docs-driven")

    if not labels:
        return (
            "未识别到明显技术栈，请按仓库实际情况补充。"
            if zh
            else "No clear stack detected; adjust based on your repository."
        )
    return " / ".join(labels)


def _collect_reference_paths(workspace: str, traits: dict[str, Any]) -> list[str]:
    if not workspace:
        return ["tui_runtime.md"]

    candidates = [
        "tui_runtime.md",
        "docs/agent/tui_runtime.md",
        "docs/product/requirements.md",
        "docs/product/product_spec.md",
        "docs/systems/",
        "docs/ux/",
        "docs/engineering/",
        "src/backend/",
        "src/frontend/",
        "backend/",
        "frontend/",
        "apps/",
        "package.json",
        "tsconfig.json",
        "pyproject.toml",
        "requirements.txt",
        "go.mod",
        "Cargo.toml",
    ]

    refs: list[str] = []
    seen = set()
    for rel in candidates:
        norm = rel.replace("\\", "/")
        full = os.path.join(workspace, norm.rstrip("/"))
        exists = os.path.isdir(full) if norm.endswith("/") else os.path.exists(full)
        if exists and norm not in seen:
            seen.add(norm)
            refs.append(norm)

    for app_name in list(traits.get("app_dirs") or [])[:6]:
        rel = f"apps/{app_name}/"
        if rel not in seen and os.path.isdir(os.path.join(workspace, "apps", app_name)):
            seen.add(rel)
            refs.append(rel)

    if not refs:
        refs.append("tui_runtime.md")
    return refs[:12]


def _collect_example_tasks(profile: str, traits: dict[str, Any]) -> list[str]:
    zh = _is_chinese_profile(profile)
    tasks: list[str] = []

    if traits.get("python"):
        tasks.append("运行 `pytest -q` 并修复失败。" if zh else "Run `pytest -q` and fix failures.")
    if traits.get("node"):
        manager = str(traits.get("package_manager") or "npm")
        tasks.append(f"运行 `{manager} test` 并修复失败。" if zh else f"Run `{manager} test` and fix failures.")
        tasks.append(
            f"运行 `{manager} run build` 并修复构建错误。" if zh else f"Run `{manager} run build` and fix build errors."
        )
    if traits.get("go"):
        tasks.append("运行 `go test ./...` 并修复失败。" if zh else "Run `go test ./...` and fix failures.")
    if traits.get("rust"):
        tasks.append("运行 `cargo test` 并修复失败。" if zh else "Run `cargo test` and fix failures.")
    if traits.get("frontend") and traits.get("backend"):
        tasks.append(
            "对齐前后端接口契约并补充一条回归测试。"
            if zh
            else "Align frontend/backend API contracts and add one regression test."
        )
    if traits.get("docs"):
        tasks.append("同步更新相关文档。" if zh else "Update related documentation.")

    if not tasks:
        tasks = (
            [
                "先识别本仓库的测试与构建命令，并补充到计划中。",
                "完成一个最小可交付改动并附带验收检查。",
            ]
            if zh
            else [
                "Identify this repo's test/build commands and add them to the plan.",
                "Deliver one minimal change with acceptance checks.",
            ]
        )
    return tasks[:6]


def _format_comment_bullets(items: list[str]) -> str:
    return "\n".join([f"# - {item}" for item in items])


def _default_plan_template(profile: str) -> str:
    if _is_chinese_profile(profile):
        return """# Zhenguan Governance 计划
# 在此写下下一批任务；保持增量与测试。
# 循环会根据仓库结构自动识别项目类型，并给出参考与示例任务。
#
# 项目类型（自动识别）：
# {{project_summary}}
#
# 建议参考（按当前仓库自动筛选）：
{{reference_list}}
#
# 示例任务（按项目类型自动生成）：
{{example_task_list}}
"""
    return """# Zhenguan Governance PLAN
# Write the next batch of tasks here.
# Keep scope small and incremental, with tests.
# The loop will detect your repository shape and suggest references/tasks.
#
# Detected project profile:
# {{project_summary}}
#
# Suggested references (auto-selected from your repo):
{{reference_list}}
#
# Example tasks (generated from detected stack):
{{example_task_list}}
"""


def _render_plan_template(template: str, profile: str, plan_path: str) -> str:
    workspace = _infer_workspace_from_plan_path(plan_path)
    traits = _detect_project_traits(workspace)
    values: dict[str, object] = {
        "project_summary": _project_summary(profile, traits),
        "reference_list": _format_comment_bullets(_collect_reference_paths(workspace, traits)),
        "example_task_list": _format_comment_bullets(_collect_example_tasks(profile, traits)),
    }

    rendered = template
    try:
        from polaris.kernelone.prompts.loader import render_template

        rendered = render_template(template, values)
    except (RuntimeError, ValueError):
        for key, value in values.items():
            rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
            rendered = rendered.replace(f"{{{{ {key} }}}}", str(value))

    return rendered if rendered.endswith("\n") else rendered + "\n"


def _resolve_plan_template(profile: str, plan_path: str = "") -> str:
    template = ""
    try:
        from polaris.kernelone.prompts.loader import get_template

        loaded = get_template("plan_template", profile=profile)
        if isinstance(loaded, str) and loaded.strip():
            template = loaded
    except (RuntimeError, ValueError):
        template = ""

    if not template or _is_legacy_game_plan_template(template):
        template = _default_plan_template(profile)
    return _render_plan_template(template, profile, plan_path)


def ensure_plan_file(path: str, auto_continue: bool = False) -> bool:
    del auto_continue
    if not str(path or "").strip():
        raise ValueError("plan path is required")
    workspace = _infer_workspace_from_plan_path(path)
    fs = _kernel_fs(workspace)
    existing_content = ""

    try:
        logical_path = fs.to_logical_path(path)
        if not fs.exists(logical_path):
            raise FileNotFoundError(f"Plan contract missing: {path}. Please create it explicitly before execution.")
        existing_content = fs.read_text(logical_path)
    except ValueError:
        # Fallback for absolute runtime paths that are valid on disk but
        # cannot be reverse-mapped to logical paths in the inferred workspace.
        absolute_path = os.path.abspath(path)
        if not os.path.isfile(absolute_path):
            raise FileNotFoundError(f"Plan contract missing: {path}. Please create it explicitly before execution.")
        try:
            with open(absolute_path, encoding="utf-8") as handle:
                existing_content = handle.read()
        except RuntimeError as exc:
            raise RuntimeError(f"Failed to read plan file as UTF-8: {path}") from exc
    except FileNotFoundError:
        raise
    except RuntimeError as exc:
        raise RuntimeError(f"Failed to read plan file as UTF-8: {path}") from exc

    if _needs_literal_newline_normalization(existing_content):
        raise RuntimeError(f"Plan file contains literal newline escapes and requires explicit fix: {path}")
    if _is_legacy_game_plan_template(existing_content):
        raise RuntimeError(f"Plan file uses legacy template and requires explicit regeneration: {path}")
    return True
