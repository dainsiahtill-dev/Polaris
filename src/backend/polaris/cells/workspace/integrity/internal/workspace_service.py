"""Workspace integrity service primitives.

This module provides:
1. Workspace bootstrap status helpers.
2. Project profile and QA command inference.
3. Deterministic docs-init template generation.

No HTTP semantics — callers map domain exceptions to HTTP at the delivery boundary.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
from typing import Any

# ACGA-compliant: Import from public facade instead of internal module
# file_io_facade re-exports from internal.file_io as a stable public interface
from polaris.cells.runtime.projection.public.file_io_facade import (
    read_json,
    read_readme_title,
)
from polaris.cells.workspace.integrity.internal.fs_utils import (
    normalize_rel_path,
    workspace_has_docs,
    workspace_status_path,
)
from polaris.domain.exceptions import ConflictError
from polaris.kernelone.fs.text_ops import write_text_atomic as _write_text_atomic
from polaris.kernelone.utils.time_utils import utc_now_str

logger = logging.getLogger(__name__)

_QA_PLACEHOLDER = "Add project-specific QA commands."
_NODE_HINT_RE = re.compile(
    r"\b(node|javascript|typescript|react|next(?:\.js)?|vite|npm|pnpm|yarn|package\.json)\b",
    re.IGNORECASE,
)
_PYTHON_HINT_RE = re.compile(
    r"\b(python|pytest|ruff|mypy|pyproject\.toml|requirements\.txt)\b",
    re.IGNORECASE,
)
_GO_HINT_RE = re.compile(r"\b(go|go\.mod|golang)\b", re.IGNORECASE)
_RUST_HINT_RE = re.compile(r"\b(rust|cargo|cargo\.toml)\b", re.IGNORECASE)


def _split_items(value: str) -> list[str]:
    if not value:
        return []
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").replace(",", "\n")
    items: list[str] = []
    for line in normalized.split("\n"):
        token = line.strip()
        if not token:
            continue
        token = token.lstrip("-").strip()
        if token:
            items.append(token)
    return items


def _format_list(items: list[str], placeholder: str = "TBD") -> str:
    if not items:
        return f"- {placeholder}"
    return "\n".join(f"- {item}" for item in items)


def _infer_profile_from_hint(hint_text: str) -> dict[str, bool]:
    text = str(hint_text or "")
    return {
        "python": bool(_PYTHON_HINT_RE.search(text)),
        "node": bool(_NODE_HINT_RE.search(text)),
        "go": bool(_GO_HINT_RE.search(text)),
        "rust": bool(_RUST_HINT_RE.search(text)),
    }


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in values:
        token = str(item or "").strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(token)
    return result


def detect_project_profile(workspace: str) -> dict[str, Any]:
    """Detect coarse project profile from workspace files."""
    profile: dict[str, Any] = {
        "python": False,
        "node": False,
        "go": False,
        "rust": False,
        "package_manager": None,
    }
    if not workspace:
        return profile

    def _exists(name: str) -> bool:
        return os.path.isfile(os.path.join(workspace, name))

    profile["python"] = _exists("pyproject.toml") or _exists("requirements.txt") or _exists("setup.py")
    profile["node"] = _exists("package.json")
    profile["go"] = _exists("go.mod")
    profile["rust"] = _exists("Cargo.toml")
    if profile["node"]:
        if _exists("pnpm-lock.yaml"):
            profile["package_manager"] = "pnpm"
        elif _exists("yarn.lock"):
            profile["package_manager"] = "yarn"
        else:
            profile["package_manager"] = "npm"
    return profile


def default_qa_commands(profile: dict[str, Any], hint_text: str = "") -> list[str]:
    """Build default QA commands from explicit profile plus textual hints."""
    inferred = _infer_profile_from_hint(hint_text)
    has_python = bool(profile.get("python")) or inferred["python"]
    has_node = bool(profile.get("node")) or inferred["node"]
    has_go = bool(profile.get("go")) or inferred["go"]
    has_rust = bool(profile.get("rust")) or inferred["rust"]
    manager = str(profile.get("package_manager") or "").strip() or "npm"

    commands: list[str] = []
    if has_python:
        commands.extend(["ruff check .", "mypy", "pytest"])
    if has_node:
        commands.append(f"{manager} test")
    if has_go:
        commands.append("go test ./...")
    if has_rust:
        commands.append("cargo test")
    if not commands:
        commands.append("python -m pytest -q")
    return _dedupe_keep_order(commands)


def _resolve_effective_qa_commands(
    profile: dict[str, Any],
    hint_text: str,
    qa_commands: list[str],
) -> list[str]:
    filtered = [
        str(cmd).strip() for cmd in list(qa_commands or []) if str(cmd).strip() and str(cmd).strip() != _QA_PLACEHOLDER
    ]
    if filtered:
        return _dedupe_keep_order(filtered)
    return default_qa_commands(profile, hint_text=hint_text)


def read_workspace_status(workspace: str) -> dict[str, Any] | None:
    path = workspace_status_path(workspace)
    if not path or not os.path.isfile(path):
        return None
    try:
        data = read_json(path)
        return data if isinstance(data, dict) else None
    except (RuntimeError, ValueError) as exc:
        logger.debug("read_workspace_status failed for %s: %s", path, exc)
        return None


def write_workspace_status(
    workspace: str,
    *,
    status: str,
    reason: str,
    actions: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    if not workspace:
        return
    payload: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "actions": actions or [],
        "workspace_path": os.path.abspath(workspace),
        "timestamp": utc_now_str(),
    }
    if isinstance(extra, dict):
        payload.update(extra)
    try:
        os.makedirs(os.path.dirname(workspace_status_path(workspace)), exist_ok=True)
        _write_text_atomic(
            workspace_status_path(workspace),
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
    except (RuntimeError, ValueError) as exc:
        logger.debug("write_workspace_status failed for %s: %s", workspace, exc)


def clear_workspace_status(workspace: str) -> None:
    path = workspace_status_path(workspace)
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except (RuntimeError, ValueError) as exc:
        logger.debug("clear_workspace_status failed for %s: %s", path, exc)


def ensure_docs_ready_or_raise(workspace: str) -> None:
    """Ensure docs/ exists in workspace; raise ConflictError if missing.

    Raises:
        ConflictError: docs/ directory not found.
    """
    if workspace_has_docs(workspace):
        clear_workspace_status(workspace)
        return
    write_workspace_status(
        workspace,
        status="NEEDS_DOCS_INIT",
        reason="docs/ directory not found",
        actions=["INIT_DOCS_WIZARD"],
    )
    raise ConflictError(
        message="workspace missing docs/. Run docs init first.",
        resource_type="workspace",
    )


def is_safe_docs_path(rel_path: str, target_root: str) -> bool:
    norm = normalize_rel_path(rel_path)
    if not norm or norm == "." or norm.startswith(".."):
        return False
    if (
        not norm.lower().startswith("docs/")
        and norm.lower() != "docs"
        and not norm.lower().startswith("workspace/docs/")
        and norm.lower() != "workspace/docs"
    ):
        return False
    target_norm = normalize_rel_path(target_root)
    return not (
        target_norm
        and target_norm not in ("docs", "workspace/docs")
        and not norm.lower().startswith(target_norm.lower().rstrip("/") + "/")
    )


def select_docs_target_root(workspace: str) -> str:
    legacy_docs_dir = os.path.join(workspace, "docs")
    if os.path.isdir(legacy_docs_dir):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        return os.path.join("workspace/docs", "_drafts", f"init-{stamp}").replace("\\", "/")
    return "workspace/docs"


def _build_interface_contract_markdown(goal_text: str, in_scope_items: list[str]) -> str:
    key_scopes = in_scope_items or ["拆解子系统边界并定义能力契约"]
    return (
        "# Interface Contract\n\n"
        "## 目标语义\n"
        f"- {goal_text}\n\n"
        "## 能力边界\n"
        f"{_format_list(key_scopes)}\n\n"
        "## 核心能力契约\n"
        "- 验证执行与证据Finalize\n"
        "- 实时事件流接入、排序校验与幂等处理\n"
        "- 状态快照与增量广播的一致性策略\n\n"
        "## 交付约束\n"
        "- 所有文本读写必须显式 UTF-8\n"
        "- 关键副作用需具备审计证据\n"
    )


def build_docs_templates(
    workspace: str,
    mode: str,
    fields: dict[str, str],
    qa_commands: list[str],
) -> dict[str, str]:
    """Build docs-init template map for product and legacy paths."""
    goal = fields.get("goal") or ""
    if mode == "import_readme" and not goal:
        goal = read_readme_title(workspace)
    goal_text = goal.strip() or "TBD"
    in_scope_items = _split_items(fields.get("in_scope") or "")
    out_scope_items = _split_items(fields.get("out_of_scope") or "")
    constraints_items = _split_items(fields.get("constraints") or "")
    dod_items = _split_items(fields.get("definition_of_done") or "")
    backlog_items = _split_items(fields.get("backlog") or "")
    hint_text = "\n".join(
        [
            goal_text,
            "\n".join(in_scope_items),
            "\n".join(backlog_items),
            "\n".join(dod_items),
        ]
    )
    effective_qa = _resolve_effective_qa_commands(
        profile=detect_project_profile(workspace),
        hint_text=hint_text,
        qa_commands=qa_commands,
    )
    qa_lines = "\n".join(f"- `{cmd}`" for cmd in effective_qa)

    readme_note = ""
    if mode == "import_readme":
        readme_note = "\n## README Reference\n- See `tui_runtime.md` for additional context.\n"

    docs: dict[str, str] = {}
    docs["docs/product/requirements.md"] = (
        "# Product Requirements\n\n"
        "## Goal\n"
        f"{goal_text}\n\n"
        "## In Scope\n"
        f"{_format_list(in_scope_items)}\n\n"
        "## Out of Scope\n"
        f"{_format_list(out_scope_items)}\n\n"
        "## Acceptance Criteria\n"
        f"{_format_list(dod_items)}\n\n"
        f"{readme_note}"
    )
    docs["docs/product/plan.md"] = (
        f"# Plan\n\n## Backlog\n{_format_list(backlog_items)}\n\n## Quality Gates\n{qa_lines}\n"
    )
    docs["docs/product/interface_contract.md"] = _build_interface_contract_markdown(
        goal_text=goal_text,
        in_scope_items=in_scope_items,
    )
    docs["docs/product/constraints.md"] = "# Constraints\n\n" + _format_list(constraints_items) + "\n"

    # Keep legacy docs layout for callers not yet switched to docs/product/*.
    docs["docs/00_overview.md"] = (
        "# Overview\n\n"
        "## Goal\n"
        f"{goal_text}\n\n"
        "## In Scope\n"
        f"{_format_list(in_scope_items)}\n\n"
        "## Out of Scope\n"
        f"{_format_list(out_scope_items)}\n"
    )
    docs["docs/10_requirements.md"] = (
        "# Requirements\n\n"
        "## Key Requirements\n"
        f"{_format_list(in_scope_items)}\n\n"
        "## Acceptance Criteria\n"
        f"{_format_list(dod_items)}\n"
    )
    docs["docs/20_constraints.md"] = "# Constraints\n\n" + _format_list(constraints_items) + "\n"
    docs["docs/30_backlog.md"] = "# Backlog\n\n" + _format_list(backlog_items) + "\n"
    docs["docs/40_quality.md"] = (
        f"# Quality\n\n## Definition of Done\n{_format_list(dod_items)}\n\n## Default QA Commands\n{qa_lines}\n"
    )
    metadata = {
        "schema_version": 2,
        "created_at": utc_now_str(),
        "docs_mode": mode,
        "requirements_path": "workspace/docs/product/requirements.md",
        "qa_commands": effective_qa,
    }
    docs["docs/.polaris.json"] = json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
    return docs
