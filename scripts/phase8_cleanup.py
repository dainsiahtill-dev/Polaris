"""Phase 8: 清理脚本

清理未接入主链路且重复的旧编排模块与脚本。
运行前请确认：
1. 所有 PM/Director 执行路径已迁移到 UnifiedOrchestrationService
2. 回归测试通过
3. 有完整备份

使用方法:
    python scripts/phase8_cleanup.py --dry-run  # 预览
    python scripts/phase8_cleanup.py --execute  # 执行清理
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple


# 待清理文件清单
FILES_TO_REMOVE: List[Tuple[str, str]] = [
    # (路径, 说明)
    ("src/backend/core/runtime_orchestrator.py", "旧版 Orchestrator（已标记 deprecated）"),
    ("src/backend/scripts/pm/nodes/__init__.py", "未使用的节点模块"),
    ("src/backend/scripts/pm/nodes/pm_nodes.py", "未使用的节点模块"),
    ("src/backend/scripts/pm/nodes/director_bridge.py", "未使用的节点模块"),
]

# 待清理的重复导入
IMPORTS_TO_CLEAN: List[Tuple[str, str, str]] = [
    # (文件, 旧导入, 新导入)
    ("src/backend/app/orchestration/workflows/pm_workflow.py",
     "from .director_workflow import DirectorWorkflow",
     "from .generic_pipeline_workflow import GenericPipelineWorkflow"),
]


def log(message: str) -> None:
    """输出日志"""
    print(f"[{datetime.now().isoformat()}] {message}")


def check_file_exists(filepath: str) -> bool:
    """检查文件是否存在"""
    path = Path(filepath)
    return path.exists()


def get_file_size(filepath: str) -> int:
    """获取文件大小"""
    try:
        return Path(filepath).stat().st_size
    except Exception:
        return 0


def remove_file(filepath: str, dry_run: bool = True) -> bool:
    """删除文件"""
    path = Path(filepath)

    if not path.exists():
        log(f"⚠️  文件不存在: {filepath}")
        return False

    if dry_run:
        log(f"[DRY-RUN] 将删除: {filepath} ({get_file_size(filepath)} bytes)")
        return True

    try:
        # 备份到 .bak
        backup_path = path.with_suffix(path.suffix + ".bak")
        path.rename(backup_path)
        log(f"✅ 已备份并删除: {filepath} -> {backup_path}")
        return True
    except Exception as e:
        log(f"❌ 删除失败: {filepath}, error={e}")
        return False


def generate_cleanup_report(dry_run: bool = True) -> str:
    """生成清理报告"""
    lines = [
        "=" * 60,
        "Polaris Phase 8 清理报告",
        f"模式: {'预览' if dry_run else '执行'}",
        f"时间: {datetime.now().isoformat()}",
        "=" * 60,
        "",
        "📁 待清理文件:",
        "-" * 60,
    ]

    total_size = 0
    for filepath, description in FILES_TO_REMOVE:
        exists = check_file_exists(filepath)
        size = get_file_size(filepath) if exists else 0
        total_size += size
        status = "✓" if exists else "✗"
        lines.append(f"  {status} {filepath}")
        lines.append(f"    说明: {description}")
        lines.append(f"    大小: {size} bytes")
        lines.append("")

    lines.extend([
        "-" * 60,
        f"总计: {total_size} bytes ({total_size / 1024:.2f} KB)",
        "",
        "📦 待更新导入:",
        "-" * 60,
    ])

    for filepath, old_import, new_import in IMPORTS_TO_CLEAN:
        exists = check_file_exists(filepath)
        status = "✓" if exists else "✗"
        lines.append(f"  {status} {filepath}")
        lines.append(f"    - {old_import}")
        lines.append(f"    + {new_import}")
        lines.append("")

    lines.extend([
        "=" * 60,
        "⚠️  注意事项:",
        "1. 清理前请确保已通过回归测试",
        "2. 清理文件会被重命名为 .bak 备份",
        "3. 如需恢复，手动将 .bak 文件重命名即可",
        "4. 清理后请重新运行架构守护测试",
        "=" * 60,
    ])

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Polaris Phase 8 清理脚本"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式（不实际删除）",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="执行清理",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="生成清理报告",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="检查旧模块引用",
    )

    args = parser.parse_args()

    # 确定工作模式
    dry_run = not args.execute

    if args.report:
        print(generate_cleanup_report(dry_run=True))
        return 0

    if args.check:
        print("🔍 检查旧模块引用...")
        # 扫描代码中是否还有对旧 orchestrator 的引用
        backend_dir = Path("src/backend")
        old_imports = [
            "from core.runtime_orchestrator import",
            "import core.runtime_orchestrator",
        ]

        found = []
        for py_file in backend_dir.rglob("*.py"):
            if py_file.name == "runtime_orchestrator.py":
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
                for import_stmt in old_imports:
                    if import_stmt in content:
                        found.append((str(py_file), import_stmt))
            except Exception:
                continue

        if found:
            print("⚠️  发现旧模块引用:")
            for filepath, import_stmt in found:
                print(f"  {filepath}: {import_stmt}")
            print("\n请先迁移这些引用再执行清理")
        else:
            print("✅ 未发现旧模块引用，可以安全清理")

        return 0

    # 生成并显示报告
    print(generate_cleanup_report(dry_run=dry_run))
    print()

    if dry_run:
        print("💡 使用 --execute 执行实际清理")
        return 0

    # 确认执行
    print("⚠️  即将执行清理操作，文件将被重命名为 .bak")
    confirm = input("确认执行? [yes/no]: ")

    if confirm.lower() != "yes":
        print("❌ 已取消")
        return 1

    # 执行清理
    success_count = 0
    for filepath, _ in FILES_TO_REMOVE:
        if remove_file(filepath, dry_run=False):
            success_count += 1

    print(f"\n✅ 清理完成: {success_count}/{len(FILES_TO_REMOVE)} 文件")

    # 运行架构守护测试
    print("\n🧪 运行架构守护测试...")
    result = os.system("python -m pytest tests/refactor/test_architecture_guard.py -v --tb=short")

    if result != 0:
        print("⚠️  架构守护测试失败，请检查")
        return 1

    print("\n🎉 Phase 8 清理完成!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
