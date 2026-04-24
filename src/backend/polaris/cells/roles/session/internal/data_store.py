"""Role Data Store - 角色数据存储

按角色分仓写入数据，支持UTF-8编码和原子写。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from polaris.cells.roles.session.internal.storage_paths import (
    resolve_preferred_logical_prefix,
)
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import KernelFileSystem

logger = logging.getLogger(__name__)


class RoleDataStoreError(Exception):
    """数据存储错误"""

    pass


class PathSecurityError(RoleDataStoreError):
    """路径安全错误"""

    pass


class RoleDataStore:
    """角色数据存储

    按角色分仓存储数据，支持：
    - UTF-8 编码（强制）
    - 原子写入
    - 写入前备份
    - 文件扩展名白名单
    - 数据保留策略

    存储结构：
        runtime/roles/<role_id>/
            ├── data/           # 数据文件
            ├── logs/           # 执行日志
            ├── outputs/        # 输出产物
            └── backups/        # 备份文件

    使用示例:
        >>> store = RoleDataStore(profile, workspace=".")
        >>> store.write_json("blueprint.json", {"version": "1.0"})
        >>> data = store.read_json("blueprint.json")
        >>> store.append_log("execution.log", "Task completed")
    """

    SUBDIRS = ["data", "logs", "outputs", "backups"]

    def __init__(self, profile: Any, workspace: str = "") -> None:
        """初始化数据存储

        Args:
            profile: 角色Profile
            workspace: 工作区路径
        """
        self.profile = profile
        self.policy = profile.data_policy
        self.workspace = Path(workspace).resolve() if workspace else Path.cwd().resolve()
        self._kernel_fs = KernelFileSystem(str(self.workspace), LocalFileSystemAdapter())

        # 构建角色数据目录
        self._base_rel_dir = resolve_preferred_logical_prefix(
            self._kernel_fs,
            runtime_prefix=f"runtime/roles/{profile.role_id}",
            workspace_fallback_prefix=f"workspace/runtime/roles/{profile.role_id}",
        )
        self.base_dir = self._kernel_fs.resolve_path(self._base_rel_dir)
        self._ensure_directories()

        if str(self.policy.encoding).strip().lower().replace("_", "-") != "utf-8":
            raise RoleDataStoreError("RoleDataStore only supports UTF-8 encoding")

    def _to_logical_path(self, filepath: Path) -> str:
        return self._kernel_fs.to_logical_path(str(filepath))

    def _ensure_directories(self) -> None:
        """确保子目录存在"""
        for subdir in self.SUBDIRS:
            (self.base_dir / subdir).mkdir(parents=True, exist_ok=True)

    @property
    def data_dir(self) -> Path:
        """数据目录"""
        return self.base_dir / "data"

    @property
    def logs_dir(self) -> Path:
        """日志目录"""
        return self.base_dir / "logs"

    @property
    def outputs_dir(self) -> Path:
        """输出目录"""
        return self.base_dir / "outputs"

    @property
    def backups_dir(self) -> Path:
        """备份目录"""
        return self.base_dir / "backups"

    def _validate_path(self, filepath: str | Path) -> Path:
        """验证路径安全性

        Args:
            filepath: 文件路径（相对或绝对）

        Returns:
            验证后的Path对象

        Raises:
            PathSecurityError: 路径不安全
        """
        filepath = Path(filepath)

        # 如果提供了绝对路径，检查是否在base_dir下
        if filepath.is_absolute():
            try:
                filepath.relative_to(self.base_dir)
            except ValueError as err:
                raise PathSecurityError(f"路径 '{filepath}' 不在角色数据目录内") from err
        else:
            # 相对路径，默认在data_dir下
            filepath = self.data_dir / filepath

        # 检查路径穿越
        resolved = filepath.resolve()
        try:
            resolved.relative_to(self.base_dir.resolve())
        except ValueError as err:
            raise PathSecurityError(f"路径 '{filepath}' 尝试访问上级目录") from err

        # 检查扩展名
        if filepath.suffix not in self.policy.allowed_extensions:
            raise PathSecurityError(f"文件扩展名 '{filepath.suffix}' 不在允许列表中: {self.policy.allowed_extensions}")

        return filepath

    def _atomic_write(self, filepath: Path, content: str) -> None:
        """原子写入文件

        使用临时文件+重命名实现原子写。
        """
        filepath_rel = self._to_logical_path(filepath)

        # 备份（如果启用且文件存在）
        if self.policy.backup_before_write and self._kernel_fs.exists(filepath_rel):
            backup_path = (
                self.backups_dir / f"{filepath.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{filepath.suffix}"
            )
            backup_rel = self._to_logical_path(backup_path)
            payload = self._kernel_fs.read_bytes(filepath_rel)
            self._kernel_fs.write_bytes(backup_rel, payload)
            logger.debug(f"Created backup: {backup_path}")

        if self.policy.atomic_write:
            # 原子写：写入临时文件，然后重命名
            temp_path = filepath.parent / f".{filepath.stem}_tmp_{uuid.uuid4().hex}{filepath.suffix}"
            temp_rel = self._to_logical_path(temp_path)
            try:
                self._kernel_fs.write_text(temp_rel, content, encoding="utf-8")
                os.replace(temp_path, filepath)
                logger.debug(f"Atomic write: {filepath}")
            except (RuntimeError, ValueError):
                self._kernel_fs.remove(temp_rel, missing_ok=True)
                raise
        else:
            # 普通写
            self._kernel_fs.write_text(filepath_rel, content, encoding="utf-8")
            logger.debug(f"Write: {filepath}")

    def write_text(self, filepath: str | Path, content: str) -> Path:
        """写入文本文件

        Args:
            filepath: 文件路径（相对data_dir或绝对路径）
            content: 文本内容

        Returns:
            写入的文件路径
        """
        filepath = self._validate_path(filepath)
        self._atomic_write(filepath, content)
        return filepath

    def write_json(self, filepath: str | Path, data: Any, indent: int = 2) -> Path:
        """写入JSON文件

        Args:
            filepath: 文件路径
            data: JSON数据
            indent: 缩进

        Returns:
            写入的文件路径
        """
        filepath = self._validate_path(filepath)

        # 确保扩展名是.json
        if filepath.suffix != ".json":
            filepath = filepath.with_suffix(".json")

        content = json.dumps(data, indent=indent, ensure_ascii=False)
        self._atomic_write(filepath, content)
        return filepath

    def write_yaml(self, filepath: str | Path, data: Any) -> Path:
        """写入YAML文件

        Args:
            filepath: 文件路径
            data: YAML数据

        Returns:
            写入的文件路径
        """
        try:
            import yaml
        except ImportError as err:
            raise ImportError("PyYAML is required for YAML writing") from err

        filepath = self._validate_path(filepath)

        # 确保扩展名是.yaml或.yml
        if filepath.suffix not in [".yaml", ".yml"]:
            filepath = filepath.with_suffix(".yaml")

        content = yaml.dump(data, allow_unicode=True, sort_keys=False)
        self._atomic_write(filepath, content)
        return filepath

    def read_text(self, filepath: str | Path) -> str:
        """读取文本文件

        Args:
            filepath: 文件路径

        Returns:
            文件内容
        """
        filepath = self._validate_path(filepath)
        filepath_rel = self._to_logical_path(filepath)
        return self._kernel_fs.read_text(filepath_rel, encoding="utf-8")

    def read_json(self, filepath: str | Path) -> Any:
        """读取JSON文件

        Args:
            filepath: 文件路径

        Returns:
            JSON数据
        """
        filepath = self._validate_path(filepath)
        content = self.read_text(filepath)
        return json.loads(content)

    def read_yaml(self, filepath: str | Path) -> Any:
        """读取YAML文件

        Args:
            filepath: 文件路径

        Returns:
            YAML数据
        """
        try:
            import yaml
        except ImportError as err:
            raise ImportError("PyYAML is required for YAML reading") from err

        filepath = self._validate_path(filepath)
        content = self.read_text(filepath)
        return yaml.safe_load(content)

    def append_log(self, logname: str, message: str, level: str = "INFO") -> Path:
        """追加日志

        Args:
            logname: 日志文件名
            message: 日志消息
            level: 日志级别

        Returns:
            日志文件路径
        """
        timestamp = datetime.now().isoformat()
        log_line = f"[{timestamp}] [{level}] {message}\n"

        log_path = self.logs_dir / logname
        log_rel = self._to_logical_path(log_path)
        self._kernel_fs.append_text(log_rel, log_line, encoding="utf-8")

        return log_path

    def append_event(self, event_type: str, data: dict[str, Any]) -> Path:
        """追加事件到事件日志

        Args:
            event_type: 事件类型
            data: 事件数据

        Returns:
            事件日志文件路径
        """
        event = {
            "timestamp": datetime.now().isoformat(),
            "role": self.profile.role_id,
            "type": event_type,
            "data": data,
        }

        # 按日期分文件
        date_str = datetime.now().strftime("%Y%m%d")
        event_file = self.logs_dir / f"events_{date_str}.jsonl"
        event_rel = self._to_logical_path(event_file)
        self._kernel_fs.append_text(
            event_rel,
            json.dumps(event, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        return event_file

    def save_output(self, name: str, content: str | bytes, metadata: dict | None = None) -> Path:
        """保存输出产物

        Args:
            name: 产物名称
            content: 产物内容
            metadata: 元数据

        Returns:
            产物文件路径
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = self.outputs_dir / f"{timestamp}_{name}"
        output_dir.mkdir(parents=True, exist_ok=True)

        # 保存内容
        if isinstance(content, str):
            output_path = output_dir / "content.txt"
            output_rel = self._to_logical_path(output_path)
            self._kernel_fs.write_text(output_rel, content, encoding="utf-8")
        else:
            output_path = output_dir / "content.bin"
            output_rel = self._to_logical_path(output_path)
            self._kernel_fs.write_bytes(output_rel, content)

        # 保存元数据
        if metadata:
            meta_path = output_dir / "metadata.json"
            meta_rel = self._to_logical_path(meta_path)
            self._kernel_fs.write_text(
                meta_rel,
                json.dumps(metadata, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        return output_dir

    def list_files(self, subdir: str = "data") -> list[Path]:
        """列出文件

        Args:
            subdir: 子目录名

        Returns:
            文件路径列表
        """
        target_dir = self.base_dir / subdir
        if not target_dir.exists():
            return []

        return list(target_dir.rglob("*"))

    def cleanup_old_data(self) -> int:
        """清理过期数据

        根据保留策略删除旧数据。

        Returns:
            删除的文件数
        """
        if self.policy.retention_days <= 0:
            return 0  # 保留策略禁用

        cutoff_date = datetime.now() - timedelta(days=self.policy.retention_days)
        deleted_count = 0

        for subdir in ["data", "logs", "outputs", "backups"]:
            target_dir = self.base_dir / subdir
            if not target_dir.exists():
                continue

            for filepath in target_dir.rglob("*"):
                if not filepath.is_file():
                    continue

                try:
                    mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
                    if mtime < cutoff_date:
                        rel = self._to_logical_path(filepath)
                        self._kernel_fs.remove(rel, missing_ok=True)
                        deleted_count += 1
                        logger.debug(f"Deleted old file: {filepath}")
                except (RuntimeError, ValueError) as e:
                    logger.warning(f"Failed to delete {filepath}: {e}")

        return deleted_count

    def get_storage_stats(self) -> dict[str, Any]:
        """获取存储统计信息

        Returns:
            统计信息字典
        """
        stats = {"role": self.profile.role_id, "base_dir": str(self.base_dir), "subdirs": {}}

        for subdir in self.SUBDIRS:
            target_dir = self.base_dir / subdir
            if not target_dir.exists():
                continue

            file_count = 0
            total_size = 0

            for filepath in target_dir.rglob("*"):
                if filepath.is_file():
                    file_count += 1
                    total_size += filepath.stat().st_size

            stats["subdirs"][subdir] = {"file_count": file_count, "total_size_bytes": total_size}

        return stats
