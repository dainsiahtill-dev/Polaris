"""
Polaris KernelOne 深入代码质量分析
"""

import ast
import logging
import re
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)


def analyze_detailed_issues():
    root = Path("polaris/kernelone")

    # 收集问题
    issues = {
        "long_functions": [],
        "high_complexity": [],
        "missing_docstrings": [],
        "TODO_FIXME": [],
        "deep_nesting": [],
        "magic_numbers": [],
        "long_param_lists": [],
    }

    for pyfile in root.rglob("*.py"):
        if "__pycache__" in str(pyfile):
            continue

        try:
            with open(pyfile, encoding="utf-8") as f:
                content = f.read()
                lines = content.split("\n")

            tree = ast.parse(content)

            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    # 检查函数长度
                    func_length = (node.end_lineno or node.lineno) - node.lineno
                    if func_length > 100:
                        issues["long_functions"].append(
                            {"file": pyfile.name, "func": node.name, "lines": func_length, "line": node.lineno}
                        )

                    # 计算复杂度
                    complexity = 1
                    for child in ast.walk(node):
                        if isinstance(child, (ast.If, ast.While, ast.For)):
                            complexity += 1

                    if complexity > 20:
                        issues["high_complexity"].append(
                            {"file": pyfile.name, "func": node.name, "complexity": complexity, "line": node.lineno}
                        )

                    # 检查参数数量
                    if len(node.args.args) > 7:
                        issues["long_param_lists"].append(
                            {"file": pyfile.name, "func": node.name, "params": len(node.args.args), "line": node.lineno}
                        )

                # 检查 TODO/FIXME
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    if hasattr(node, "body") and node.body:
                        first_line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
                        if "TODO" in first_line or "FIXME" in first_line:
                            issues["TODO_FIXME"].append(
                                {
                                    "file": pyfile.name,
                                    "type": "Function" if isinstance(node, ast.FunctionDef) else "Class",
                                    "name": node.name,
                                    "line": node.lineno,
                                }
                            )

            # 检查魔数
            for i, line in enumerate(lines):
                match = re.search(r"\b\d{3,}\b", line)
                if match and "=" in line:
                    # 跳过明显是版本号、端口号等合法用途
                    line_lower = line.lower()
                    if not any(kw in line_lower for kw in ["version", "port", "timeout", "retry", "max_", "min_"]):
                        issues["magic_numbers"].append(
                            {"file": pyfile.name, "line": i + 1, "content": line.strip()[:60]}
                        )

        except Exception as e:
            logger.debug("Error analyzing detailed issues in %s: %s", pyfile.name, e)

    return issues


def find_code_duplicates():
    """查找代码重复"""

    logger.debug("#查找重复代码")

    root = Path("polaris/kernelone")
    patterns = {}

    for pyfile in root.rglob("*.py"):
        if "__pycache__" in str(pyfile):
            continue

        try:
            with open(pyfile, encoding="utf-8") as f:
                lines = f.readlines()

            # 检测相似的函数定义
            for i, line in enumerate(lines):
                # 检测 return 模式
                stripped = line.strip()
                if stripped.startswith("return ") and len(stripped) > 20:
                    key = stripped[:40]
                    if key not in patterns:
                        patterns[key] = []
                    patterns[key].append((pyfile.name, i + 1))

        except Exception as e:
            logger.debug("Error finding duplicates in %s: %s", pyfile.name, e)

    # 找出重复的
    duplicates = {k: v for k, v in patterns.items() if len(v) >= 3}
    return duplicates


def analyze_naming():
    """分析命名规范问题"""
    root = Path("polaris/kernelone")
    issues = {
        "camelCase_funcs": [],
        "snake_case_classes": [],
        "inconsistent_constants": [],
    }

    for pyfile in root.rglob("*.py"):
        if "__pycache__" in str(pyfile):
            continue

        try:
            with open(pyfile, encoding="utf-8") as f:
                content = f.read()

            tree = ast.parse(content)

            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    # 函数应该是 snake_case
                    if re.match(r"^[a-z_]+$", node.name) is None and not node.name.startswith("_"):
                        if re.search(r"[A-Z]", node.name):
                            issues["camelCase_funcs"].append(
                                {"file": pyfile.name, "func": node.name, "line": node.lineno}
                            )

                elif isinstance(node, ast.ClassDef):
                    # 类应该是 PascalCase
                    if re.match(r"^[A-Z][a-zA-Z0-9]*$", node.name) is None:
                        issues["snake_case_classes"].append(
                            {"file": pyfile.name, "class": node.name, "line": node.lineno}
                        )

        except Exception as e:
            logger.debug("Error analyzing naming in %s: %s", pyfile.name, e)

    return issues


def print_report():
    issues = analyze_detailed_issues()
    duplicates = find_code_duplicates()
    naming = analyze_naming()

    print("=" * 80)
    print("POLARIS/KERNELONE 深度代码质量审计报告 (续)")
    print("=" * 80)
    print()

    # 1. 过长函数
    print("-" * 80)
    print("【问题1】过长函数 (>100 行) - 需要拆分")
    print("-" * 80)
    sorted_funcs = sorted(issues["long_functions"], key=lambda x: x["lines"], reverse=True)
    for item in sorted_funcs[:15]:
        print(f"  [严重] {item['file']}::{item['func']}")
        print(f"          行数: {item['lines']}, 位置: 第{item['line']}行")
    print()

    # 2. 高复杂度函数
    print("-" * 80)
    print("【问题2】高复杂度函数 (>20) - 需要重构")
    print("-" * 80)
    sorted_complex = sorted(issues["high_complexity"], key=lambda x: x["complexity"], reverse=True)
    for item in sorted_complex[:15]:
        print(f"  [警告] {item['file']}::{item['func']}")
        print(f"          复杂度: {item['complexity']}, 位置: 第{item['line']}行")
    print()

    # 3. 长参数列表
    print("-" * 80)
    print("【问题3】长参数列表 (>7个参数) - 考虑使用 dataclass 或 dict")
    print("-" * 80)
    for item in issues["long_param_lists"][:15]:
        print(f"  [建议] {item['file']}::{item['func']} - {item['params']} 个参数")
    print()

    # 4. 魔数
    print("-" * 80)
    print("【问题4】硬编码魔数 - 提取为命名常量")
    print("-" * 80)
    counter = Counter()
    for item in issues["magic_numbers"][:100]:
        counter[item["content"][:30]] += 1

    for content, count in counter.most_common(10):
        print(f'  [建议] "{content}..." - 出现 {count} 次')
    print()

    # 5. TODO/FIXME
    print("-" * 80)
    print("【问题5】TODO/FIXME 标记 - 技术债务")
    print("-" * 80)
    print(f"  共发现 {len(issues['TODO_FIXME'])} 处标记")
    for item in issues["TODO_FIXME"][:10]:
        print(f"  [债务] {item['file']}::{item['name']} (第{item['line']}行)")
    print()

    # 6. 命名问题
    print("-" * 80)
    print("【问题6】命名规范违规")
    print("-" * 80)
    print(f"  CamelCase 函数: {len(naming['camelCase_funcs'])} 处")
    for item in naming["camelCase_funcs"][:5]:
        print(f"    [违规] {item['file']}::{item['func']}")
    print()

    # 7. 重复代码模式
    print("-" * 80)
    print("【问题7】重复代码模式 (出现>=3次)")
    print("-" * 80)
    sorted_dups = sorted(duplicates.items(), key=lambda x: len(x[1]), reverse=True)
    for pattern, locations in sorted_dups[:8]:
        print(f"  Pattern: {pattern[:50]}")
        for loc in locations[:3]:
            print(f"    -> {loc[0]}:{loc[1]}")
        if len(locations) > 3:
            print(f"    ... 共 {len(locations)} 处")
    print()


if __name__ == "__main__":
    print_report()
