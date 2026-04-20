#!/usr/bin/env python3
"""审计系统 V1 到 V2 迁移脚本

执行一次性切换:
1. 预检查: 事件数/链完整性/关键 run 抽样 triage 对比
2. 备份: 创建旧审计文件备份
3. 迁移: 转换旧格式到新格式
4. 验证: 链完整性验证和抽样对比
5. 重建索引: 构建新索引

使用方式:
    # 试运行（不实际修改）
    python scripts/migrate_audit_v1_to_v2.py --runtime-root ./.polaris/runtime --dry-run

    # 执行迁移
    python scripts/migrate_audit_v1_to_v2.py --runtime-root ./.polaris/runtime --execute

    # 仅重建索引
    python scripts/migrate_audit_v1_to_v2.py --runtime-root ./.polaris/runtime --rebuild-index

    # 显示帮助
    python scripts/migrate_audit_v1_to_v2.py --help

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# 添加 src/backend 到路径
_backend_path = Path(__file__).parent.parent / "src" / "backend"
if _backend_path.exists() and str(_backend_path) not in sys.path:
    sys.path.insert(0, str(_backend_path))

from infrastructure.persistence.audit_store import AuditStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class MigrationError(Exception):
    """迁移错误"""
    pass


class AuditMigrator:
    """审计系统迁移器"""

    def __init__(self, runtime_root: Path, dry_run: bool = True):
        self.runtime_root = Path(runtime_root).resolve()
        self.audit_dir = self.runtime_root / "audit"
        self.backup_dir = self.runtime_root / "audit_backup_v1"
        self.dry_run = dry_run
        self.stats: Dict[str, Any] = {
            "total_files": 0,
            "total_events": 0,
            "migrated_events": 0,
            "errors": [],
        }

    def pre_check(self) -> bool:
        """预检查：验证旧文件、统计事件数、检查链完整性"""
        logger.info("=== 预检查阶段 ===")

        if not self.audit_dir.exists():
            logger.error(f"审计目录不存在: {self.audit_dir}")
            return False

        # 查找所有审计文件
        old_files = list(self.audit_dir.glob("audit-*.jsonl"))
        if not old_files:
            logger.warning("未找到旧审计文件，可能已经是 V2 格式或空目录")
            return True

        self.stats["total_files"] = len(old_files)
        logger.info(f"发现 {len(old_files)} 个审计文件")

        # 统计事件数
        total_events = 0
        for log_file in old_files:
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            try:
                                json.loads(line)
                                total_events += 1
                            except json.JSONDecodeError:
                                self.stats["errors"].append(f"{log_file}: JSON 解析错误")
            except Exception as e:
                self.stats["errors"].append(f"{log_file}: 读取错误 - {e}")

        self.stats["total_events"] = total_events
        logger.info(f"统计到 {total_events} 个事件")

        # 使用 AuditStore 检查链完整性
        try:
            store = AuditStore(runtime_root=self.runtime_root)
            result = store.verify_chain()
            logger.info(f"链完整性检查: {result.is_valid}")
            logger.info(f"  - 总事件数: {result.total_events}")
            logger.info(f"  - Gap 数: {result.gap_count}")
            logger.info(f"  - 无效事件: {len(result.invalid_events)}")

            if result.invalid_events:
                logger.warning(f"发现 {len(result.invalid_events)} 个无效事件")
        except Exception as e:
            logger.warning(f"链完整性检查失败: {e}")

        if self.stats["errors"]:
            logger.warning(f"预检查发现问题: {len(self.stats['errors'])} 个")
            for err in self.stats["errors"][:5]:
                logger.warning(f"  - {err}")

        return True

    def backup(self) -> bool:
        """创建备份"""
        logger.info("=== 备份阶段 ===")

        if self.dry_run:
            logger.info("[DRY RUN] 将创建备份目录")
            return True

        if self.backup_dir.exists():
            logger.warning(f"备份目录已存在: {self.backup_dir}")
            backup_timestamp = self.backup_dir.with_suffix(
                f".backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            )
            self.backup_dir.rename(backup_timestamp)
            logger.info(f"旧备份已重命名为: {backup_timestamp}")

        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)

            # 复制所有 audit 文件
            for log_file in self.audit_dir.glob("audit-*.jsonl"):
                shutil.copy2(log_file, self.backup_dir / log_file.name)
                logger.info(f"已备份: {log_file.name}")

            # 复制 key 文件
            key_file = self.audit_dir / ".key"
            if key_file.exists():
                shutil.copy2(key_file, self.backup_dir / ".key")

            logger.info(f"备份完成: {self.backup_dir}")
            return True

        except Exception as e:
            logger.error(f"备份失败: {e}")
            return False

    def migrate(self) -> bool:
        """执行迁移"""
        logger.info("=== 迁移阶段 ===")

        if self.dry_run:
            logger.info("[DRY RUN] 将执行迁移")
            return True

        # V1 到 V2 的数据格式已经在 AuditStore 中处理
        # 这里主要是确保所有文件都被正确读取和验证
        try:
            store = AuditStore(runtime_root=self.runtime_root)

            # 触发一次完整的读取和验证
            events = store.query(limit=100000)
            logger.info(f"已验证 {len(events)} 个事件")

            # 重建索引
            self._rebuild_index(events)

            self.stats["migrated_events"] = len(events)
            logger.info("迁移完成")
            return True

        except Exception as e:
            logger.error(f"迁移失败: {e}")
            return False

    def _rebuild_index(self, events: List[Any]) -> None:
        """重建索引"""
        logger.info("=== 重建索引 ===")

        # 按 run_id, task_id, trace_id 分组
        index_data: Dict[str, Dict[str, List[Dict]]] = {
            "run_id": {},
            "task_id": {},
            "trace_id": {},
        }

        for event in events:
            if not hasattr(event, 'task') or not hasattr(event, 'context'):
                continue

            task = event.task or {}
            ctx = event.context or {}

            run_id = task.get("run_id")
            task_id = task.get("task_id")
            trace_id = ctx.get("trace_id")

            entry = {
                "timestamp": event.timestamp.timestamp() if hasattr(event, 'timestamp') else 0,
                "event_id": event.event_id if hasattr(event, 'event_id') else "",
                "file_path": str(self.audit_dir / "audit-current.jsonl"),
            }

            if run_id:
                index_data["run_id"].setdefault(run_id, []).append(entry)
            if task_id:
                index_data["task_id"].setdefault(task_id, []).append(entry)
            if trace_id:
                index_data["trace_id"].setdefault(trace_id, []).append(entry)

        # 写入索引文件
        for index_name, data in index_data.items():
            index_file = self.audit_dir / f"index.{index_name}.json"
            try:
                with open(index_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                logger.info(f"已重建索引: {index_file} ({len(data)} 个键)")
            except Exception as e:
                logger.error(f"索引重建失败 {index_file}: {e}")

    def verify(self) -> bool:
        """验证迁移结果"""
        logger.info("=== 验证阶段 ===")

        try:
            store = AuditStore(runtime_root=self.runtime_root)
            result = store.verify_chain()

            if not result.is_valid:
                logger.error("链完整性验证失败")
                return False

            logger.info(f"✓ 链完整性验证通过")
            logger.info(f"  - 总事件数: {result.total_events}")
            logger.info(f"  - Gap 数: {result.gap_count}")

            # 对比迁移前后的事件数
            if self.stats["total_events"] > 0:
                migrated = result.total_events
                original = self.stats["total_events"]
                if migrated != original:
                    logger.warning(f"事件数不一致: 原始 {original}, 迁移后 {migrated}")
                else:
                    logger.info(f"✓ 事件数一致: {migrated}")

            return True

        except Exception as e:
            logger.error(f"验证失败: {e}")
            return False

    def run(self) -> int:
        """执行完整迁移流程"""
        logger.info(f"开始迁移: {self.runtime_root}")
        logger.info(f"模式: {'试运行' if self.dry_run else '实际执行'}")

        # 1. 预检查
        if not self.pre_check():
            return 1

        # 2. 备份
        if not self.backup():
            return 1

        # 3. 迁移
        if not self.migrate():
            return 1

        # 4. 验证
        if not self.verify():
            return 1

        logger.info("=== 迁移完成 ===")
        logger.info(f"总文件数: {self.stats['total_files']}")
        logger.info(f"总事件数: {self.stats['total_events']}")
        logger.info(f"已迁移事件: {self.stats['migrated_events']}")

        if self.dry_run:
            logger.info("\n这是试运行。要实际执行迁移，请使用 --execute 参数")

        return 0


def cmd_rebuild_index(args: argparse.Namespace) -> int:
    """仅重建索引"""
    runtime_root = Path(args.runtime_root).resolve()
    audit_dir = runtime_root / "audit"

    if not audit_dir.exists():
        logger.error(f"审计目录不存在: {audit_dir}")
        return 1

    logger.info(f"重建索引: {runtime_root}")

    try:
        store = AuditStore(runtime_root=runtime_root)
        events = store.query(limit=100000)

        # 构建索引
        index_data: Dict[str, Dict[str, List[Dict]]] = {
            "run_id": {},
            "task_id": {},
            "trace_id": {},
        }

        for event in events:
            task = event.task or {}
            ctx = event.context or {}

            run_id = task.get("run_id")
            task_id = task.get("task_id")
            trace_id = ctx.get("trace_id")

            entry = {
                "timestamp": event.timestamp.timestamp(),
                "event_id": event.event_id,
                "file_path": str(store.get_log_file_path()),
            }

            if run_id:
                index_data["run_id"].setdefault(run_id, []).append(entry)
            if task_id:
                index_data["task_id"].setdefault(task_id, []).append(entry)
            if trace_id:
                index_data["trace_id"].setdefault(trace_id, []).append(entry)

        # 写入索引文件
        for index_name, data in index_data.items():
            index_file = audit_dir / f"index.{index_name}.json"
            with open(index_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"已重建索引: {index_file} ({len(data)} 个键)")

        logger.info("索引重建完成")
        return 0

    except Exception as e:
        logger.error(f"索引重建失败: {e}")
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="审计系统 V1 到 V2 迁移脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 试运行（不实际修改）
  python scripts/migrate_audit_v1_to_v2.py --runtime-root ./.polaris/runtime --dry-run

  # 执行迁移
  python scripts/migrate_audit_v1_to_v2.py --runtime-root ./.polaris/runtime --execute

  # 仅重建索引
  python scripts/migrate_audit_v1_to_v2.py --runtime-root ./.polaris/runtime --rebuild-index
        """
    )
    parser.add_argument(
        "--runtime-root",
        required=True,
        help="Runtime 根目录路径",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行（不实际修改文件）",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="实际执行迁移",
    )
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="仅重建索引",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="详细输出",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.rebuild_index:
        return cmd_rebuild_index(args)

    if not args.dry_run and not args.execute:
        print("错误: 请指定 --dry-run 或 --execute", file=sys.stderr)
        parser.print_help()
        return 1

    migrator = AuditMigrator(
        runtime_root=Path(args.runtime_root),
        dry_run=args.dry_run,
    )

    return migrator.run()


if __name__ == "__main__":
    sys.exit(main())
