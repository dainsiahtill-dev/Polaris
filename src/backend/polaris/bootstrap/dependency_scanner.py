"""
跨Cell导入违规扫描器

用于扫描和报告polaris/cells目录下的跨Cell导入违规。
"""

import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Violation:
    """单个违规记录"""

    file_path: str
    line_number: int
    import_line: str
    source_cell: str
    target_cell: str
    violation_type: str  # 'type_a', 'type_b', 'type_c'


@dataclass
class ScanResult:
    """扫描结果"""

    violations: list[Violation] = field(default_factory=list)
    type_a_count: int = 0  # Cell A -> Cell B.internal
    type_b_count: int = 0  # Cell -> kernelone.internal
    type_c_count: int = 0  # Cross-cell direct import (non-public)

    def by_source_cell(self) -> dict[str, list[Violation]]:
        """按源Cell分组"""
        result = defaultdict(list)
        for v in self.violations:
            result[v.source_cell].append(v)
        return dict(result)

    def by_target_cell(self) -> dict[str, list[Violation]]:
        """按目标Cell分组"""
        result = defaultdict(list)
        for v in self.violations:
            result[v.target_cell].append(v)
        return dict(result)


class DependencyScanner:
    """依赖违规扫描器"""

    # 正则表达式模式
    FROM_IMPORT_PATTERN = re.compile(r"^from\s+polaris\.cells\.([^.]+)\.([^.]+)", re.MULTILINE)
    INTERNAL_IMPORT_PATTERN = re.compile(
        r"from\s+polaris\.cells\.[^.]+\.[^.]+\.internal",
    )
    KERNELONE_INTERNAL_PATTERN = re.compile(
        r"from\s+polaris\.kernelone\.[^\s]+\.internal",
    )

    def __init__(self, base_path: str = "polaris/cells"):
        self.base_path = Path(base_path)
        self.result = ScanResult()

    def _get_cell_name(self, file_path: Path) -> str:
        """从文件路径提取Cell名称"""
        # 路径格式: polaris/cells/{cell}/{submodule}/...
        parts = file_path.parts
        try:
            cells_idx = parts.index("cells")
            if len(parts) > cells_idx + 1:
                return parts[cells_idx + 1]
        except ValueError:
            pass
        return "unknown"

    def _get_target_cell(self, import_line: str) -> tuple[str, str]:
        """从导入语句提取目标Cell和子模块"""
        match = self.FROM_IMPORT_PATTERN.search(import_line)
        if match:
            return match.group(1), match.group(2)
        return "", ""

    def _is_violation(self, file_path: Path, import_line: str) -> tuple[bool, str]:
        """
        检查是否为违规导入
        返回: (是否违规, 违规类型)
        """
        source_cell = self._get_cell_name(file_path)
        target_cell, target_submodule = self._get_target_cell(import_line)

        # 跳过同一Cell内的导入
        if source_cell == target_cell:
            return False, ""

        # 类型B: Cell -> kernelone.internal
        if self.KERNELONE_INTERNAL_PATTERN.search(import_line):
            return True, "type_b"

        # 类型A: Cell A -> Cell B.internal
        if self.INTERNAL_IMPORT_PATTERN.search(import_line):
            return True, "type_a"

        # 类型C: 跨Cell直接导入 (非public)
        if target_cell and ".public" not in import_line:
            # 检查是否是internal导入
            if ".internal" in import_line:
                return True, "type_a"
            # 其他非public导入
            return True, "type_c"

        return False, ""

    def scan_file(self, file_path: Path) -> list[Violation]:
        """扫描单个文件"""
        violations = []
        source_cell = self._get_cell_name(file_path)

        try:
            content = file_path.read_text(encoding="utf-8")
            lines = content.split("\n")

            for line_num, line in enumerate(lines, 1):
                line = line.strip()
                if not line.startswith("from polaris.cells."):
                    continue

                is_violation, vtype = self._is_violation(file_path, line)
                if is_violation:
                    target_cell, _ = self._get_target_cell(line)
                    violations.append(
                        Violation(
                            file_path=str(file_path),
                            line_number=line_num,
                            import_line=line,
                            source_cell=source_cell,
                            target_cell=target_cell,
                            violation_type=vtype,
                        )
                    )
        except (RuntimeError, ValueError) as e:
            print(f"Error scanning {file_path}: {e}", file=sys.stderr)

        return violations

    def scan(self) -> ScanResult:
        """执行全量扫描"""
        py_files = list(self.base_path.rglob("*.py"))

        for file_path in py_files:
            violations = self.scan_file(file_path)
            self.result.violations.extend(violations)

        # 统计
        for v in self.result.violations:
            if v.violation_type == "type_a":
                self.result.type_a_count += 1
            elif v.violation_type == "type_b":
                self.result.type_b_count += 1
            elif v.violation_type == "type_c":
                self.result.type_c_count += 1

        return self.result

    def print_report(self):
        """打印扫描报告"""
        print("=" * 80)
        print("跨Cell导入违规扫描报告")
        print("=" * 80)
        print(f"\n总计违规: {len(self.result.violations)}")
        print(f"  - 类型A (Cell A → Cell B.internal): {self.result.type_a_count}")
        print(f"  - 类型B (Cell → kernelone.internal): {self.result.type_b_count}")
        print(f"  - 类型C (跨Cell非public导入): {self.result.type_c_count}")

        # 按源Cell统计
        print("\n按源Cell统计:")
        by_source = self.result.by_source_cell()
        for cell, violations in sorted(by_source.items(), key=lambda x: -len(x[1]))[:15]:
            print(f"  {cell}: {len(violations)}")

        # 按目标Cell统计
        print("\n按目标Cell统计 (Top 15):")
        by_target = self.result.by_target_cell()
        for cell, violations in sorted(by_target.items(), key=lambda x: -len(x[1]))[:15]:
            print(f"  {cell}: {len(violations)}")

        # 类型A违规详情
        if self.result.type_a_count > 0:
            print("\n类型A违规详情 (Sample):")
            type_a_violations = [v for v in self.result.violations if v.violation_type == "type_a"][:10]
            for v in type_a_violations:
                print(f"  {v.file_path}:{v.line_number}")
                print(f"    {v.import_line[:80]}")


def main():
    """主入口"""
    scanner = DependencyScanner()
    scanner.scan()
    scanner.print_report()

    # 返回非零退出码如果有违规
    if scanner.result.violations:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
