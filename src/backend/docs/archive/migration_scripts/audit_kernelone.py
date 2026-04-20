"""
Polaris KernelOne 代码质量审计分析脚本
"""

import ast
import logging
import re
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)


class CodeQualityAnalyzer:
    def __init__(self, root_dir):
        self.root_dir = Path(root_dir)
        self.stats = {
            "files": 0,
            "total_lines": 0,
            "functions": 0,
            "classes": 0,
            "complexity": defaultdict(int),
            "complexity_details": [],
            "long_functions": [],
            "large_files": [],
            "bare_excepts": 0,
            "exception_blocks": 0,
            "duplicate_patterns": defaultdict(list),
            "naming_issues": [],
        }

    def calculate_complexity(self, node):
        """计算圈复杂度"""
        complexity = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.While, ast.For, ast.ExceptHandler)):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
        return complexity

    def check_naming_convention(self, name, node_type):
        """检查命名规范"""
        issues = []
        if node_type == "function" or node_type == "method":
            if not name.islower() and not name.startswith("_"):
                if re.match(r"^[A-Z]", name):
                    issues.append(f"Function '{name}' should use snake_case")
        elif node_type == "class":
            if name and not name[0].isupper():
                issues.append(f"Class '{name}' should use PascalCase")
        return issues

    def analyze_file(self, filepath):
        """分析单个文件"""
        try:
            with open(filepath, encoding="utf-8") as f:
                source = f.read()

            tree = ast.parse(source)
            file_lines = len(source.split("\n"))

            # 检查文件大小
            if file_lines > 500:
                self.stats["large_files"].append(
                    {"path": str(filepath.relative_to(self.root_dir)), "lines": file_lines}
                )

            file_complexity = 0

            # 遍历 AST
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    self.stats["functions"] += 1
                    complexity = self.calculate_complexity(node)
                    file_complexity += complexity

                    self.stats["complexity_details"].append(
                        {"file": filepath.name, "name": node.name, "complexity": complexity, "lineno": node.lineno}
                    )

                    # 检查函数长度
                    func_lines = node.end_lineno - node.lineno if node.end_lineno else 1
                    if func_lines > 100:
                        self.stats["long_functions"].append(
                            {"file": filepath.name, "name": node.name, "lines": func_lines, "complexity": complexity}
                        )

                    # 检查命名
                    self.stats["naming_issues"].extend(self.check_naming_convention(node.name, "function"))

                elif isinstance(node, ast.ClassDef):
                    self.stats["classes"] += 1
                    # 检查类命名
                    self.stats["naming_issues"].extend(self.check_naming_convention(node.name, "class"))

                elif isinstance(node, ast.ExceptHandler):
                    self.stats["exception_blocks"] += 1
                    if node.type is None:
                        self.stats["bare_excepts"] += 1

            self.stats["complexity"][filepath.name] = file_complexity
            self.stats["files"] += 1
            self.stats["total_lines"] += file_lines

            # 检测重复模式
            self._detect_duplicates(source, filepath)

        except SyntaxError:
            logger.debug("Syntax error in %s: skipping", filepath.name)
        except Exception as e:
            logger.debug("Error analyzing file %s: %s", filepath.name, e)

    def _detect_duplicates(self, source, filepath):
        """检测重复代码模式"""
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if len(line.strip()) > 30:
                key = line.strip()[:50]
                self.stats["duplicate_patterns"][key].append(f"{filepath.name}:{i + 1}")

    def run(self):
        """运行分析"""
        python_files = list(self.root_dir.rglob("*.py"))
        python_files = [f for f in python_files if "__pycache__" not in str(f)]

        for f in python_files:
            self.analyze_file(f)

        return self.stats


def main():
    print("=" * 70)
    print("POLARIS/KERNELONE 代码质量审计报告")
    print("=" * 70)

    analyzer = CodeQualityAnalyzer("polaris/kernelone")
    stats = analyzer.run()

    print()
    print(f"总文件数: {stats['files']}")
    print(f"总代码行数: {stats['total_lines']}")
    print(f"总函数数: {stats['functions']}")
    print(f"总类数: {stats['classes']}")
    print()

    print("-" * 70)
    print("1. 圈复杂度统计 (Top 15 文件)")
    print("-" * 70)
    sorted_complexity = sorted(stats["complexity"].items(), key=lambda x: x[1], reverse=True)[:15]
    for fname, complexity in sorted_complexity:
        print(f"  {fname}: {complexity}")
    print()

    print("-" * 70)
    print("2. 高复杂度函数 (Top 20)")
    print("-" * 70)
    sorted_funcs = sorted(stats["complexity_details"], key=lambda x: x["complexity"], reverse=True)[:20]
    for func in sorted_funcs:
        print(f"  {func['file']}::{func['name']} (行{func['lineno']}): 复杂度 {func['complexity']}")
    print()

    print("-" * 70)
    print("3. 过长函数 (>100 行)")
    print("-" * 70)
    if stats["long_functions"]:
        for func in stats["long_functions"]:
            print(f"  {func['file']}::{func['name']}: {func['lines']} 行, 复杂度: {func['complexity']}")
    else:
        print("  无")
    print()

    print("-" * 70)
    print("4. 过大文件 (>500 行)")
    print("-" * 70)
    if stats["large_files"]:
        for f in stats["large_files"]:
            print(f"  {f['path']}: {f['lines']} 行")
    else:
        print("  无")
    print()

    print("-" * 70)
    print("5. 异常处理统计")
    print("-" * 70)
    print(f"  异常处理块总数: {stats['exception_blocks']}")
    print(f"  裸 except 块: {stats['bare_excepts']}")
    if stats["exception_blocks"] > 0:
        pct = stats["bare_excepts"] / stats["exception_blocks"] * 100
        print(f"  裸 except 占比: {pct:.1f}%")
    print()

    print("-" * 70)
    print("6. 潜在重复代码 (出现3次以上)")
    print("-" * 70)
    duplicates = [(k, v) for k, v in stats["duplicate_patterns"].items() if len(v) >= 3]
    duplicates.sort(key=lambda x: len(x[1]), reverse=True)
    for pattern, locations in duplicates[:10]:
        print(f"  Pattern: {pattern[:40]}...")
        for loc in locations[:3]:
            print(f"    - {loc}")
        if len(locations) > 3:
            print(f"    ... 还有 {len(locations) - 3} 处")
    print()


if __name__ == "__main__":
    main()
