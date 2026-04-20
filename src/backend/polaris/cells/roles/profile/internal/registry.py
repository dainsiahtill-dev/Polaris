"""Role Profile Registry - 角色配置注册表

角色的单一事实来源（SSOT），支持从 YAML/JSON 文件加载角色配置。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

try:
    import yaml

    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.fs.text_ops import write_text_atomic

from .schema import RoleProfile, profile_from_dict, profile_to_dict

logger = logging.getLogger(__name__)


class RoleProfileRegistry:
    """角色Profile注册表

    统一管理所有角色的配置，支持从文件加载和运行时注册。

    使用示例:
        >>> registry = RoleProfileRegistry()
        >>> registry.load_from_yaml("config/core_roles.yaml")
        >>> pm_profile = registry.get_profile("pm")
        >>> all_roles = registry.get_all_profiles()
    """

    # 核心角色ID列表
    CORE_ROLES = ["pm", "architect", "chief_engineer", "director", "qa"]

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._profiles: dict[str, RoleProfile] = {}
        self._loaded_files: list[str] = []

    def register(self, profile: RoleProfile) -> None:
        """注册一个角色Profile

        Args:
            profile: 角色配置

        Raises:
            ValueError: 如果 profile 无效
        """
        if not profile.role_id:
            raise ValueError("role_id 不能为空")

        if not profile.display_name:
            raise ValueError("display_name 不能为空")

        # 验证提示词策略约束
        if profile.prompt_policy.allow_override:
            raise ValueError(f"角色 {profile.role_id} 的 allow_override 必须为 False （禁止覆盖核心提示词）")

        with self._lock:
            self._profiles[profile.role_id] = profile
        logger.debug(f"Registered role profile: {profile.role_id}@{profile.version}")

    def get_profile(self, role_id: str) -> RoleProfile | None:
        """获取角色Profile

        Args:
            role_id: 角色标识

        Returns:
            RoleProfile 或 None（如果不存在）
        """
        with self._lock:
            return self._profiles.get(role_id)

    def get_profile_or_raise(self, role_id: str) -> RoleProfile:
        """获取角色Profile，不存在则抛出异常

        Args:
            role_id: 角色标识

        Returns:
            RoleProfile

        Raises:
            ValueError: 如果角色不存在
        """
        with self._lock:
            profile = self._profiles.get(role_id)
        if profile is None:
            with self._lock:
                available = list(self._profiles.keys())
            raise ValueError(f"未知角色: {role_id}。可用角色: {available}")
        return profile

    def get_all_profiles(self) -> dict[str, RoleProfile]:
        """获取所有角色Profile

        Returns:
            {role_id: RoleProfile} 字典
        """
        with self._lock:
            return self._profiles.copy()

    def list_roles(self) -> list[str]:
        """列出所有已注册的角色ID

        Returns:
            角色ID列表
        """
        with self._lock:
            return list(self._profiles.keys())

    def has_role(self, role_id: str) -> bool:
        """检查角色是否存在

        Args:
            role_id: 角色标识

        Returns:
            是否存在
        """
        with self._lock:
            return role_id in self._profiles

    def load_from_yaml(self, filepath: str | Path) -> int:
        """从YAML文件加载角色配置

        Args:
            filepath: YAML文件路径

        Returns:
            加载的角色数量

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: YAML格式错误或配置无效
        """
        if not YAML_AVAILABLE:
            raise ImportError("PyYAML is required for YAML loading. Install: pip install pyyaml")

        filepath = Path(filepath)
        fs = KernelFileSystem(str(filepath.parent), get_default_adapter())
        rel_path = filepath.name

        if not fs.workspace_exists(rel_path):
            raise FileNotFoundError(f"角色配置文件不存在: {filepath}")

        content = fs.workspace_read_text(rel_path, encoding="utf-8")
        data = yaml.safe_load(content)

        if not isinstance(data, dict):
            raise ValueError(f"YAML格式错误: 期望dict，实际 {type(data)}")

        # 支持两种格式:
        # 1. {role_id: {...profile...}}
        # 2. {roles: [{role_id: ...}, ...]}

        count = 0
        if "roles" in data:
            # 格式2: 角色列表
            for role_data in data["roles"]:
                profile = self._dict_to_profile(role_data)
                self.register(profile)
                count += 1
        else:
            # 格式1: 角色字典
            for role_id, role_data in data.items():
                if isinstance(role_data, dict):
                    role_data["role_id"] = role_id
                    profile = self._dict_to_profile(role_data)
                    self.register(profile)
                    count += 1

        self._loaded_files.append(str(filepath))
        logger.info(f"Loaded {count} role profiles from {filepath}")
        return count

    def load_from_json(self, filepath: str | Path) -> int:
        """从JSON文件加载角色配置

        Args:
            filepath: JSON文件路径

        Returns:
            加载的角色数量
        """
        filepath = Path(filepath)
        fs = KernelFileSystem(str(filepath.parent), get_default_adapter())
        rel_path = filepath.name

        if not fs.workspace_exists(rel_path):
            raise FileNotFoundError(f"角色配置文件不存在: {filepath}")

        content = fs.workspace_read_text(rel_path, encoding="utf-8")
        data = json.loads(content)

        count = 0
        if "roles" in data:
            for role_data in data["roles"]:
                profile = self._dict_to_profile(role_data)
                self.register(profile)
                count += 1
        else:
            for role_id, role_data in data.items():
                if isinstance(role_data, dict):
                    role_data["role_id"] = role_id
                    profile = self._dict_to_profile(role_data)
                    self.register(profile)
                    count += 1

        with self._lock:
            self._loaded_files.append(str(filepath))
        logger.info(f"Loaded {count} role profiles from {filepath}")
        return count

    def save_to_yaml(self, filepath: str | Path) -> None:
        """保存所有角色配置到YAML文件

        Args:
            filepath: 输出文件路径
        """
        if not YAML_AVAILABLE:
            raise ImportError("PyYAML is required for YAML saving")

        filepath = Path(filepath)
        with self._lock:
            profiles_to_save = [profile_to_dict(p) for p in self._profiles.values()]
        data = {"version": "1.0.0", "roles": profiles_to_save}

        content = yaml.dump(data, allow_unicode=True, sort_keys=False)
        write_text_atomic(str(filepath), content, encoding="utf-8")
        with self._lock:
            count = len(self._profiles)
        logger.info(f"Saved {count} role profiles to {filepath}")

    def save_to_json(self, filepath: str | Path, indent: int = 2) -> None:
        """保存所有角色配置到JSON文件

        Args:
            filepath: 输出文件路径
            indent: JSON缩进
        """
        filepath = Path(filepath)
        with self._lock:
            profiles_to_save = [profile_to_dict(p) for p in self._profiles.values()]
        data = {"version": "1.0.0", "roles": profiles_to_save}

        content = json.dumps(data, indent=indent, ensure_ascii=False)
        write_text_atomic(str(filepath), content, encoding="utf-8")
        with self._lock:
            count = len(self._profiles)
        logger.info(f"Saved {count} role profiles to {filepath}")

    def _dict_to_profile(self, data: dict[str, Any]) -> RoleProfile:
        """将字典转换为RoleProfile（处理嵌套结构）"""
        # 处理策略嵌套
        if "prompt_policy" in data:
            data["prompt_policy"] = self._ensure_dict(data["prompt_policy"])
        if "tool_policy" in data:
            data["tool_policy"] = self._ensure_dict(data["tool_policy"])
        if "context_policy" in data:
            data["context_policy"] = self._ensure_dict(data["context_policy"])
        if "data_policy" in data:
            data["data_policy"] = self._ensure_dict(data["data_policy"])
        if "library_policy" in data:
            data["library_policy"] = self._ensure_dict(data["library_policy"])

        return profile_from_dict(data)

    def _ensure_dict(self, value: Any) -> dict[str, Any]:
        """确保值为字典"""
        if isinstance(value, dict):
            return value
        return {}

    def validate_all(self) -> list[str]:
        """验证所有角色配置

        Returns:
            错误信息列表（空列表表示全部有效）
        """
        errors = []

        with self._lock:
            profiles_snapshot = dict(self._profiles)

        for role_id, profile in profiles_snapshot.items():
            # 验证核心角色有完整配置
            if role_id in self.CORE_ROLES:
                if not profile.prompt_policy.core_template_id:
                    errors.append(f"{role_id}: core_template_id 不能为空")

                if not profile.tool_policy.whitelist and role_id != "qa":
                    errors.append(f"{role_id}: 核心角色应该有工具白名单")

        # 验证所有核心角色都已注册
        with self._lock:
            registered = set(self._profiles.keys())
        missing = set(self.CORE_ROLES) - registered
        if missing:
            errors.append(f"缺少核心角色: {missing}")

        return errors

    def get_loaded_files(self) -> list[str]:
        """获取已加载的配置文件列表"""
        with self._lock:
            return self._loaded_files.copy()

    def reset_for_testing(self) -> None:
        """Reset for test isolation."""
        with self._lock:
            self._profiles.clear()
            self._loaded_files.clear()


# ═══════════════════════════════════════════════════════════════════════════
# 全局注册表实例
# ═══════════════════════════════════════════════════════════════════════════

# 全局单例
registry = RoleProfileRegistry()


def load_core_roles(config_dir: str | None = None) -> RoleProfileRegistry:
    """加载核心角色配置

    Args:
        config_dir: 配置目录路径（默认优先查找 config/roles）

    Returns:
        已加载的注册表实例
    """
    if config_dir is None:
        # 从当前文件位置推导
        backend_dir = Path(__file__).parent.parent
        candidate_dirs = [
            backend_dir / "config" / "roles",
            backend_dir / "internal" / "config",
        ]
    else:
        candidate_dirs = [Path(config_dir)]

    loaded = False
    for candidate_dir in candidate_dirs:
        yaml_file = candidate_dir / "core_roles.yaml"
        json_file = candidate_dir / "core_roles.json"
        if yaml_file.exists():
            registry.load_from_yaml(yaml_file)
            loaded = True
            break
        if json_file.exists():
            registry.load_from_json(json_file)
            loaded = True
            break

    if not loaded:
        logger.warning("No core_roles config found in %s", ", ".join(str(item) for item in candidate_dirs))
        # 加载内置默认配置
        _load_builtin_profiles(registry)
    else:
        # SSOT: 即使从外部配置加载，也要从 llm_config.json 填充 provider_id 和 model
        _ensure_role_model_bindings(registry)

    return registry


def _ensure_role_model_bindings(reg: RoleProfileRegistry) -> None:
    """确保核心角色的 provider_id 和 model 从 llm_config.json 填充。

    SSOT: llm_config.json 是角色模型绑定的唯一真相来源。
    核心角色如果配置缺失，抛出 ValueError。非核心角色可以没有绑定。
    """
    from dataclasses import replace

    from polaris.kernelone.llm.runtime_config import get_role_model

    for role_id in reg.list_roles():
        profile = reg.get_profile(role_id)
        if profile is None:
            continue
        # 检查是否已有有效的模型绑定
        if profile.provider_id and profile.model:
            continue
        # 从 llm_config.json 填充
        provider_id, model = get_role_model(role_id)
        if provider_id and model:
            # 使用 dataclasses.replace 创建新profile（RoleProfile是不可变的frozen）
            updated_profile = replace(profile, provider_id=provider_id, model=model)
            # 重新注册更新后的profile（register会覆盖已存在的）
            reg.register(updated_profile)
            logger.debug(f"Role '{role_id}': enriched model binding {provider_id}/{model} from llm_config.json")
        elif role_id in _CORE_ROLES:
            # SSOT: 核心角色必须有模型绑定
            raise ValueError(
                f"Role '{role_id}': no model binding found in llm_config.json. "
                f"Please configure roles.{role_id}.provider_id and roles.{role_id}.model "
                f"in config/llm/llm_config.json (global config)"
            )
        # 非核心角色可以没有模型绑定


# Core roles that MUST have model bindings from llm_config.json
_CORE_ROLES = {"pm", "director", "qa", "architect", "chief_engineer"}


def _load_builtin_profiles(reg: RoleProfileRegistry) -> None:
    """加载内置默认角色配置（当外部配置不存在时使用）

    SSOT: 从 llm_config.json 读取 provider_id 和 model 配置，
    确保核心角色创建时就包含正确的模型绑定信息。
    """
    from polaris.kernelone.llm.runtime_config import get_role_model

    from .builtin_profiles import BUILTIN_PROFILES

    for profile_data in BUILTIN_PROFILES:
        role_id = str(profile_data.get("role_id") or "")
        if not role_id:
            raise ValueError(f"Builtin profile missing role_id: {profile_data}")

        # SSOT: 从 llm_config.json 获取 provider_id 和 model
        provider_id, model = get_role_model(role_id)
        if provider_id and model:
            # 复制数据避免修改原始配置，填充模型信息
            profile_data = dict(profile_data)
            profile_data["provider_id"] = provider_id
            profile_data["model"] = model
            logger.debug(f"Role '{role_id}': loaded model binding {provider_id}/{model} from llm_config.json")
        elif role_id in _CORE_ROLES:
            # SSOT: 核心角色必须有模型绑定，否则报错
            raise ValueError(
                f"Role '{role_id}': no model binding found in llm_config.json. "
                f"Please configure roles.{role_id}.provider_id and roles.{role_id}.model "
                f"in config/llm/llm_config.json (global config)"
            )
        # 非核心角色（如scout）可以没有模型绑定，跳过即可

        profile = profile_from_dict(profile_data)
        reg.register(profile)

    logger.info(f"Loaded {len(BUILTIN_PROFILES)} builtin role profiles")
