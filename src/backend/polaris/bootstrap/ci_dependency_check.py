"""
CI依赖检查脚本

用于在CI中检查跨Cell导入违规。
返回非零退出码表示发现违规。
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Violation:
    """单个违规记录"""

    file_path: str
    line_number: int
    import_line: str
    source_cell: str
    target_cell: str
    violation_type: str


class DependencyChecker:
    """依赖违规检查器"""

    def __init__(self, base_path: str = "polaris/cells") -> None:
        self.base_path = Path(base_path)
        self.violations: list[Violation] = []

    def _get_cell_name(self, file_path: Path) -> str:
        """从文件路径提取Cell名称"""
        parts = file_path.parts
        try:
            cells_idx = parts.index("cells")
            if len(parts) > cells_idx + 1:
                return parts[cells_idx + 1]
        except ValueError:
            pass
        return "unknown"

    def check_file(self, file_path: Path) -> list[Violation]:
        """检查单个文件"""
        violations = []
        source_cell = self._get_cell_name(file_path)

        try:
            content = file_path.read_text(encoding="utf-8")
            lines = content.split("\n")

            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                if not line.startswith("from polaris.cells."):
                    continue

                # 提取目标cell
                match = re.search(r"from polaris\.cells\.([^.]+)", line)
                if not match:
                    continue

                target_cell = match.group(1)

                # 跳过同一Cell内的导入
                if source_cell == target_cell:
                    continue

                # 检查是否是internal导入
                if ".internal." in line:
                    violations.append(
                        Violation(
                            file_path=str(file_path),
                            line_number=line_num,
                            import_line=line,
                            source_cell=source_cell,
                            target_cell=target_cell,
                            violation_type="cross_cell_internal",
                        )
                    )
        except (RuntimeError, ValueError) as e:
            print(f"Error checking {file_path}: {e}", file=sys.stderr)

        return violations

    def check_all(self, include_tests: bool = False) -> list[Violation]:
        """检查所有文件"""
        py_files = list(self.base_path.rglob("*.py"))

        for file_path in py_files:
            # 跳过测试文件（除非明确包含）
            if not include_tests and "/tests/" in str(file_path):
                continue

            violations = self.check_file(file_path)
            self.violations.extend(violations)

        return self.violations

    def print_report(self):
        """打印检查报告"""
        print("=" * 80)
        print("跨Cell导入违规检查报告")
        print("=" * 80)

        non_test_violations = [v for v in self.violations if "/tests/" not in v.file_path]
        test_violations = [v for v in self.violations if "/tests/" in v.file_path]

        print(f"\n非测试文件违规: {len(non_test_violations)}")
        print(f"测试文件违规: {len(test_violations)}")
        print(f"总计: {len(self.violations)}")

        if non_test_violations:
            print("\n非测试文件违规详情:")
            for v in non_test_violations[:20]:
                print(f"  {v.file_path}:{v.line_number}")
                print(f"    {v.import_line[:80]}")

        if test_violations:
            print("\n测试文件违规详情 (Sample):")
            for v in test_violations[:10]:
                print(f"  {v.file_path}:{v.line_number}")
                print(f"    {v.import_line[:80]}")


def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(description="Check cross-cell import violations")
    parser.add_argument("--include-tests", action="store_true", help="Include test files in the check")
    parser.add_argument("--fail-on-tests", action="store_true", help="Also fail if test files have violations")
    args = parser.parse_args()

    checker = DependencyChecker()
    checker.check_all(include_tests=args.include_tests)
    checker.print_report()

    # 确定退出码
    non_test_violations = [v for v in checker.violations if "/tests/" not in v.file_path]

    if non_test_violations:
        print(f"\n发现 {len(non_test_violations)} 个非测试文件违规！")
        return 1

    if args.fail_on_tests:
        test_violations = [v for v in checker.violations if "/tests/" in v.file_path]
        if test_violations:
            print(f"\n发现 {len(test_violations)} 个测试文件违规！")
            return 1

    print("\n检查通过！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
