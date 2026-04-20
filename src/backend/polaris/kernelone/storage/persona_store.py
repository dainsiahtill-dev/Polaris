"""Workspace persona store — 项目级 persona 选择持久化。

遵循 KernelOne Storage Layout 规范：
- 存储路径：workspace/.polaris/role_persona.json
- 生命周期：PERMANENT（项目级持久化，随 git 走）
- 首次加载：随机选择 persona 并固化
- 后续加载：直接读取已保存的 persona_id
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from polaris.kernelone.storage.layout import resolve_workspace_persistent_path

PERSONA_STORE_FILE = "role_persona.json"


def get_workspace_persona_store_path(workspace: str) -> Path:
    """获取 persona store 文件路径

    路径：<workspace>/.polaris/role_persona.json
    """
    resolved = resolve_workspace_persistent_path(workspace, f"workspace/{PERSONA_STORE_FILE}")
    return Path(resolved)


def load_workspace_persona(workspace: str, persona_ids: list[str]) -> str:
    """加载 workspace 的固化 persona

    首次加载时：随机选择并固化到 role_persona.json
    后续加载：直接读取已保存的 persona_id

    Args:
        workspace: 工作区路径
        persona_ids: 可选的 persona ID 列表

    Returns:
        选中的 persona_id
    """
    store_path = get_workspace_persona_store_path(workspace)

    # 已有固化 persona，直接返回
    if store_path.exists():
        try:
            with open(store_path, encoding="utf-8") as f:
                data = json.load(f)
            saved_id = data.get("persona_id", "")
            if saved_id and saved_id in persona_ids:
                return saved_id
        except (json.JSONDecodeError, OSError):
            pass

    # 首次加载：随机选择并固化
    if not persona_ids:
        return "default"

    selected = random.choice(persona_ids)

    try:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(store_path, "w", encoding="utf-8") as f:
            json.dump({"persona_id": selected}, f, ensure_ascii=False, indent=2)
    except OSError:
        # 写入失败（如只读文件系统），返回选择但不固化
        pass

    return selected


def clear_workspace_persona(workspace: str) -> None:
    """清除 workspace 的固化 persona（下一次加载会重新随机）"""
    store_path = get_workspace_persona_store_path(workspace)
    if store_path.exists():
        store_path.unlink(missing_ok=True)
