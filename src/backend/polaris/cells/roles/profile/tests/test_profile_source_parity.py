"""Guard: BUILTIN_PROFILES must stay derived from core_roles.yaml (single SSOT).

历史债务: builtin_profiles.py 曾手写一份与 core_roles.yaml 平行的角色配置,
两者静默漂移(scout_probe / repo_rg 等只落在其中一份)。现在 BUILTIN_PROFILES
直接派生自 YAML;本测试在有人重新硬编码出分叉副本时立即失败(fail-closed)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from polaris.cells.roles.profile.internal.builtin_profiles import BUILTIN_PROFILES

_YAML = Path(__file__).resolve().parents[1] / "config" / "roles" / "core_roles.yaml"


def _yaml_roles() -> list[dict[str, Any]]:
    data = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    return data["roles"]


def test_builtin_profiles_match_yaml_ssot() -> None:
    """BUILTIN_PROFILES 必须与 core_roles.yaml 的 roles 完全一致(同源)。"""
    assert _yaml_roles() == BUILTIN_PROFILES


def test_builtin_and_yaml_same_role_ids() -> None:
    builtin_ids = [p["role_id"] for p in BUILTIN_PROFILES]
    yaml_ids = [r["role_id"] for r in _yaml_roles()]
    assert builtin_ids == yaml_ids


def test_tool_whitelists_identical_per_role() -> None:
    """逐角色比对工具白名单——这正是历史漂移最先发生的地方。"""
    yaml_by_id = {r["role_id"]: r for r in _yaml_roles()}
    for profile in BUILTIN_PROFILES:
        rid = profile["role_id"]
        assert profile["tool_policy"]["whitelist"] == yaml_by_id[rid]["tool_policy"]["whitelist"], (
            f"{rid}: builtin whitelist diverged from core_roles.yaml SSOT"
        )
