#!/usr/bin/env python3
"""测试文件位置检查门禁

确保测试文件不在 src/backend/ 目录下。

Usage:
    python check_no_test_in_src.py [--fix]
    python check_no_test_in_src.py --strict  # 也检查导入
"""

from __future__ import annotations

import argparse
import os
import re
import sys

# 禁止模式：测试文件正则
TEST_FILE_PATTERNS = [
    re.compile(r"^test_.+\.py$"),  # test_*.py
    re.compile(r"^.+_test\.py$"),  # *_test.py
    re.compile(r"^tests\.py$"),  # tests.py
    re.compile(r"^conftest\.py$"),  # conftest.py
]

# 禁止目录（相对路径）
FORBIDDEN_PREFIXES = [
    "src/backend",
]

# 允许的测试根目录
ALLOWED_TEST_ROOT = "tests"


def find_test_files_in_forbidden_dirs() -> list[tuple[str, str]]:
    """查找禁止目录中的测试文件

    Returns:
        违规文件列表，每项为 (文件路径, 匹配模式)
    """
    violations: list[tuple[str, str]] = []

    for root, dirs, files in os.walk("."):
        # 跳过隐藏目录
        dirs[:] = [d for d in dirs if not d.startswith(".")]

        # 检查是否在允许的测试目录下
        rel_root = os.path.relpath(root)
        if rel_root.startswith(ALLOWED_TEST_ROOT + os.sep) or rel_root == ALLOWED_TEST_ROOT:
            continue

        # 检查是否在禁止目录下
        for prefix in FORBIDDEN_PREFIXES:
            if rel_root.startswith(prefix) or rel_root == prefix:
                for file in files:
                    for pattern in TEST_FILE_PATTERNS:
                        if pattern.match(file):
                            rel_path = os.path.join(rel_root, file)
                            violations.append((rel_path, pattern.pattern))
                            break
                break

    return violations


def check_test_imports_in_source() -> list[tuple[str, str]]:
    """检查源代码中是否有测试相关的导入

    Returns:
        违规导入列表，每项为 (文件路径, 导入语句)
    """
    violations: list[tuple[str, str]] = []

    source_files: list[str] = []
    for root, dirs, files in os.walk("polaris/delivery/cli/visualization"):
        for file in files:
            if file.endswith(".py") and not file.startswith("test_"):
                source_files.append(os.path.join(root, file))

    test_import_pattern = re.compile(
        r"(?:from\s+tests?\.|import\s+tests?)[.\w]*test",
        re.IGNORECASE,
    )

    for filepath in source_files:
        try:
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError):
            continue

        for match in test_import_pattern.finditer(content):
            violations.append((filepath, match.group()))

    return violations


def print_violations(
    violations: list[tuple[str, str]],
    category: str,
) -> None:
    """打印违规列表"""
    print(f"【{category}】")
    for path, detail in violations:
        print(f"  - {path}")
        if detail:
            print(f"    匹配: {detail}")
    print()


def main() -> int:
    """主函数

    Returns:
        0 = 通过, 1 = 失败
    """
    parser = argparse.ArgumentParser(
        description="检查测试文件位置门禁",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python check_no_test_in_src.py           # 检查文件位置
  python check_no_test_in_src.py --strict  # 严格模式（也检查导入）
  python check_no_test_in_src.py --fix     # 显示修复建议
        """,
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="严格模式（也检查导入）",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="显示修复建议（不自动修复）",
    )
    args = parser.parse_args()

    all_violations: list[tuple[str, list[tuple[str, str]]]] = []

    # 检查文件位置
    file_violations = find_test_files_in_forbidden_dirs()
    if file_violations:
        all_violations.append(("FILE_LOCATION", file_violations))

    # 检查导入（可选）
    if args.strict:
        import_violations = check_test_imports_in_source()
        if import_violations:
            all_violations.append(("TEST_IMPORTS", import_violations))

    # 报告结果
    if not all_violations:
        print("✅ 门禁通过: 无测试文件违规")
        print()
        print("规范说明:")
        print("  - 测试文件必须放在 tests/ 目录")
        print("  - 禁止在 src/backend/ 下创建测试文件")
        print("  - 源代码位于 polaris/delivery/cli/visualization/")
        return 0

    print("❌ 门禁失败: 发现以下违规")
    print()

    has_violations = False
    for category, violations in all_violations:
        if violations:
            print_violations(violations, category)
            has_violations = True

    if args.fix:
        print("修复建议:")
        print("  1. 将测试文件移动到 tests/ 目录")
        print("  2. 更新测试文件中的导入路径")
        print()
        print("示例:")
        print("  mv src/backend/.../test_xxx.py tests/delivery/cli/visualization/test_xxx.py")

    print()
    print("运行 --fix 获取详细修复建议")

    return 1


if __name__ == "__main__":
    sys.exit(main())
