"""Builtin Role Profiles —— core_roles.yaml(SSOT) 的派生视图。

历史上本模块手写了一份与 ``config/roles/core_roles.yaml`` 平行的角色配置。
运行时实际只加载 YAML(``load_core_roles`` -> ``load_from_yaml``),手写副本仅在
YAML 缺失时作为 fallback。两份配置会静默地各自演化(dual-source drift):
``scout_probe`` / ``repo_rg`` 等就曾只出现在其中一份里,导致角色在运行时拿不到
本应拥有的工具。

为消除该债务,``BUILTIN_PROFILES`` 现在直接从 ``core_roles.yaml`` 派生:
YAML 是唯一事实来源(SSOT),本模块只把它解析为 ``list[dict]``,供
``_load_builtin_profiles`` 的 fallback 路径与测试复用。两份配置不可能再漂移。

禁止在此重新手写角色配置;任何角色能力变更都应改 ``core_roles.yaml``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# core_roles.yaml 相对本文件的位置:
#   <profile>/internal/builtin_profiles.py  ->  <profile>/config/roles/core_roles.yaml
_CORE_ROLES_YAML: Path = Path(__file__).resolve().parent.parent / "config" / "roles" / "core_roles.yaml"


def _load_builtin_profiles_from_yaml() -> list[dict[str, Any]]:
    """从 core_roles.yaml(SSOT) 解析内置角色配置列表。

    Returns:
        角色配置字典列表,结构与历史手写 ``BUILTIN_PROFILES`` 完全一致
        (``role_id`` / ``display_name`` / ``prompt_policy`` / ``tool_policy`` /
        ``context_policy`` / ``data_policy`` / ``library_policy`` / ``version`` ...)。

    Raises:
        RuntimeError: SSOT 文件缺失或格式非法。fail-closed —— 唯一事实来源必须
            存在;若静默返回空列表,fallback 将注册不出任何角色,把根因隐藏到
            下游 "缺少核心角色" 的报错里。
    """
    if not _CORE_ROLES_YAML.exists():
        raise RuntimeError(
            f"core_roles.yaml (role-profile SSOT) not found at {_CORE_ROLES_YAML}. "
            "BUILTIN_PROFILES is derived from it and cannot be built without it."
        )

    data = yaml.safe_load(_CORE_ROLES_YAML.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("roles"), list):
        raise RuntimeError(
            f"core_roles.yaml has unexpected shape (expected a mapping with a 'roles' list): {_CORE_ROLES_YAML}"
        )

    roles = data["roles"]
    if not all(isinstance(item, dict) for item in roles):
        raise RuntimeError(f"core_roles.yaml 'roles' must be a list of mappings: {_CORE_ROLES_YAML}")

    return roles


# 从 SSOT 派生(模块加载时一次性解析)。禁止在此处重新硬编码角色配置,
# 否则双源漂移会复活;参见 test_profile_source_parity.py。
BUILTIN_PROFILES: list[dict[str, Any]] = _load_builtin_profiles_from_yaml()
