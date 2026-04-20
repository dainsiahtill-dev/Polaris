#!/usr/bin/env python3
"""
精确编辑器 - AST 与字符串替换的平衡实现
符合 AGENTS.md v2.2 规范的 S1 Patch 精确替换
"""

import os
import ast
import re
import time
import subprocess
import sys
from pathlib import Path
from typing import Tuple, List

class PrecisionEditor:
    """精确编辑器 - 在安全性和实用性之间平衡"""
    
    def __init__(self):
        self.context_lines = 3  # 上下文行数
        self.backup_dir = self._resolve_backup_dir()
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_backup_dir(self) -> Path:
        """Resolve backup dir outside workspace docs/ to avoid repo pollution."""
        override = os.environ.get("HP_PRECISION_BACKUP_DIR", "").strip()
        if override:
            return Path(override).expanduser()
        try:
            repo_root = Path(__file__).resolve().parents[2]
            storage_dir = repo_root / "src" / "backend" / "core" / "polaris_loop"
            if storage_dir.is_dir() and str(storage_dir) not in sys.path:
                sys.path.insert(0, str(storage_dir))
            from storage_layout import resolve_runtime_path  # type: ignore

            return Path(
                resolve_runtime_path(
                    os.getcwd(),
                    "runtime/agent_core/rollback",
                )
            )
        except Exception:
            return Path.home() / ".polaris" / "runtime" / "agent_core" / "rollback"
    
    def should_use_string_replacement(self, file_path: str, change_type: str) -> bool:
        """判断是否应该使用字符串替换"""
        
        # S1 Patch 下的允许场景
        allowed_scenarios = [
            "simple_variable_rename",
            "import_statement_add", 
            "single_line_comment",
            "config_value_change",
            "function_signature_docstring"
        ]
        
        if change_type not in allowed_scenarios:
            return False
        
        # 检查文件复杂度
        if self._is_complex_file(file_path):
            return False
        
        return True
    
    def _is_complex_file(self, file_path: str) -> bool:
        """检查文件是否过于复杂"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 复杂度指标
            lines = content.split('\n')
            if len(lines) > 500:  # 文件过长
                return True
            
            # 检查嵌套深度
            try:
                tree = ast.parse(content)
                max_depth = self._calculate_ast_depth(tree)
                if max_depth > 10:  # 嵌套过深
                    return True
            except Exception:
                # AST 解析失败，说明文件复杂
                return True
            
            return False
            
        except Exception:
            return True  # 保守策略：复杂时返回 True
    
    def _calculate_ast_depth(self, node, depth=0):
        """计算 AST 深度"""
        if not hasattr(node, 'body'):
            return depth
        
        max_child_depth = depth
        for child in ast.iter_child_nodes(node):
            child_depth = self._calculate_ast_depth(child, depth + 1)
            max_child_depth = max(max_child_depth, child_depth)
        
        return max_child_depth
    
    def precision_replace(self, file_path: str, old_pattern: str, new_text: str, 
                         context_validation: bool = True) -> Tuple[bool, str]:
        """精确替换 - 带上下文验证"""
        
        try:
            if old_pattern == "":
                return False, "Empty search pattern is not allowed for existing files"

            # 1. 创建备份
            backup_path = self._create_backup(file_path)
            
            # 2. 读取文件
            with open(file_path, 'r', encoding='utf-8', newline='') as f:
                content = f.read()
            
            # 3. 查找匹配位置（支持多行与 CRLF/LF 差异）
            effective_pattern, occurrences = self._resolve_pattern(content, old_pattern)
            if occurrences == 0:
                return False, "Pattern not found"
            if occurrences > 1:
                return False, f"Multiple matches found ({occurrences}), ambiguous"

            match_offset = content.find(effective_pattern)
            if match_offset < 0:
                return False, "Pattern not found"
            
            # 4. 上下文验证
            if context_validation:
                if not self._validate_context(content, match_offset, effective_pattern):
                    return False, "Context validation failed"
            
            # 5. 执行替换
            normalized_new_text = self._normalize_newlines_for_content(new_text, content)
            updated_content = content.replace(effective_pattern, normalized_new_text, 1)
            
            # 6. 写入文件
            with open(file_path, 'w', encoding='utf-8', newline='') as f:
                f.write(updated_content)
            
            # 7. 后置质量门禁
            if not self._run_quality_gates(file_path):
                # 仅允许人工显式确认后回滚，禁止自动回滚。
                manual_rollback = os.environ.get(
                    "HP_PRECISION_MANUAL_ROLLBACK_CONFIRMED", ""
                ).strip().lower() in {"1", "true", "yes", "on"}
                if manual_rollback:
                    self._restore_backup(file_path, backup_path)
                    return False, "Quality gates failed, rolled back (manual confirmation)"
                return False, "Quality gates failed; changes kept for manual fix (no auto rollback)"
            
            line_no = self._line_number_for_offset(content, match_offset)
            return True, f"Replaced text starting at line {line_no}"
            
        except Exception as e:
            return False, f"Error: {str(e)}"

    def _resolve_pattern(self, content: str, pattern: str) -> Tuple[str, int]:
        """在内容中解析可匹配的 pattern 及其出现次数（兼容 LF/CRLF 差异）。"""
        candidates: List[str] = [pattern]
        newline_style = self._detect_newline_style(content)
        if "\n" in pattern and "\r\n" in content and "\r\n" not in pattern:
            candidates.append(pattern.replace("\n", "\r\n"))
        if "\r\n" in pattern and "\r\n" not in content:
            candidates.append(pattern.replace("\r\n", "\n"))
        # 兼容末尾换行差异（常见于模型输出与文件真实内容不一致）
        for candidate in list(candidates):
            trimmed = candidate.rstrip("\r\n")
            if trimmed and trimmed != candidate:
                candidates.append(trimmed)
            if trimmed and newline_style:
                candidates.append(trimmed + newline_style)

        # 优先选择“精确命中一次”的候选
        seen: set[str] = set()
        occurrence_map: dict[str, int] = {}
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            occurrences = content.count(candidate)
            occurrence_map[candidate] = occurrences
            if occurrences == 1:
                return candidate, 1

        # 兜底：允许轻微空白差异（行尾空白、空行数量、CRLF/LF）
        fuzzy_matches = self._find_fuzzy_matches(content, pattern)
        if len(fuzzy_matches) == 1:
            return fuzzy_matches[0], 1
        if len(fuzzy_matches) > 1:
            return fuzzy_matches[0], len(fuzzy_matches)

        # 回退：返回出现次数最多的候选用于错误提示
        selected = pattern
        max_occurrences = 0
        for candidate, occurrences in occurrence_map.items():
            if occurrences > max_occurrences:
                selected = candidate
                max_occurrences = occurrences
        return selected, max_occurrences

    def _find_fuzzy_matches(self, content: str, pattern: str) -> List[str]:
        """查找与 pattern 近似匹配的真实片段（允许轻微空白差异）。"""
        matches: List[str] = []
        search_lines = pattern.splitlines()
        content_lines = content.splitlines()
        if not search_lines or not content_lines:
            return matches

        first_search_line = search_lines[0].strip()
        if not first_search_line:
            return matches

        # 1) 行级匹配：忽略首尾空白
        for i, line in enumerate(content_lines):
            if line.strip() != first_search_line:
                continue
            if i + len(search_lines) > len(content_lines):
                continue

            hit = True
            for j, search_line in enumerate(search_lines):
                if content_lines[i + j].strip() != search_line.strip():
                    hit = False
                    break
            if not hit:
                continue

            candidate = "\n".join(content_lines[i : i + len(search_lines)])
            candidate = self._normalize_newlines_for_content(candidate, content)
            if candidate:
                matches.append(candidate)

        if matches:
            return matches

        # 2) 容忍空行数量漂移：按非空行构造宽松正则
        non_empty_search_lines = [line.strip() for line in search_lines if line.strip()]
        if len(non_empty_search_lines) < 2:
            return matches

        tolerant_pattern = re.escape(non_empty_search_lines[0])
        for token in non_empty_search_lines[1:]:
            tolerant_pattern += r"(?:\r?\n[ \t]*)+" + re.escape(token)

        for fuzzy in re.finditer(tolerant_pattern, content):
            candidate = str(fuzzy.group(0))
            if candidate:
                matches.append(candidate)
        return matches

    def _normalize_newlines_for_content(self, text: str, content: str) -> str:
        """按目标文件主导换行风格规范化替换文本，避免混合换行。"""
        if "\n" not in text and "\r" not in text:
            return text

        newline_style = self._detect_newline_style(content)
        if newline_style == "\r\n":
            return text.replace("\r\n", "\n").replace("\n", "\r\n")
        if newline_style == "\n":
            return text.replace("\r\n", "\n")
        return text

    def _detect_newline_style(self, content: str) -> str:
        """检测内容的主导换行风格。"""
        crlf_count = content.count("\r\n")
        lf_count = content.count("\n")
        if lf_count == 0:
            return ""
        if crlf_count > 0 and crlf_count == lf_count:
            return "\r\n"
        return "\n"

    def _line_number_for_offset(self, content: str, offset: int) -> int:
        """将字符偏移转换为 1-based 行号。"""
        if offset <= 0:
            return 1
        return content.count("\n", 0, offset) + 1
    
    def _find_matches(self, lines: List[str], pattern: str) -> List[int]:
        """查找匹配的行"""
        matches = []
        
        for i, line in enumerate(lines):
            if pattern in line:
                # 更精确的匹配：确保不是注释或字符串中的内容
                if self._is_code_line(line, pattern):
                    matches.append(i)
        
        return matches
    
    def _is_code_line(self, line: str, pattern: str) -> bool:
        """检查是否是代码行（非注释）"""
        stripped = line.strip()
        
        # 跳过注释行
        if stripped.startswith('#') or stripped.startswith('//'):
            return False
        
        # 跳过字符串中的内容（简化检查）
        if '"' in line or "'" in line:
            # 更复杂的字符串检测可以在这里实现
            pass
        
        return True
    
    def _validate_context(self, content: str, match_offset: int, pattern: str) -> bool:
        """验证匹配位置仍与目标 pattern 精确一致。"""
        if match_offset < 0:
            return False
        end = match_offset + len(pattern)
        if end > len(content):
            return False
        return content[match_offset:end] == pattern
    
    def _run_quality_gates(self, file_path: str) -> bool:
        """运行后置质量门禁"""
        
        # 根据文件类型选择检查工具
        if file_path.endswith('.py'):
            return self._run_python_quality_gates(file_path)
        elif file_path.endswith(('.ts', '.tsx', '.js', '.jsx')):
            return self._run_js_quality_gates(file_path)
        
        return True  # 其他文件类型跳过
    
    def _run_python_quality_gates(self, file_path: str) -> bool:
        """Python 质量门禁"""
        try:
            # 先做 Python 语法检查（硬门禁）
            syntax_result = subprocess.run(
                [sys.executable, "-m", "py_compile", file_path],
                capture_output=True,
                text=True,
            )
            if syntax_result.returncode != 0:
                detail = (syntax_result.stderr or syntax_result.stdout or "").strip()
                print(f"Python syntax check failed: {detail}", file=sys.stderr)
                return False

            # Ruff 仅检查语法级/致命问题（不以风格类问题阻断）。
            ruff_syntax_result = subprocess.run(
                ['ruff', 'check', '--select', 'E9,F63,F7,F82', file_path],
                capture_output=True,
                text=True,
            )
            if ruff_syntax_result.returncode != 0:
                detail = (
                    ruff_syntax_result.stderr
                    or ruff_syntax_result.stdout
                    or ""
                ).strip()
                print(f"Ruff syntax check failed: {detail}", file=sys.stderr)
                return False

            # 可选严格 lint 门禁（默认关闭，避免风格问题阻断任务落地）。
            strict_lint = os.environ.get("HP_PRECISION_STRICT_LINT", "").strip().lower()
            if strict_lint in {"1", "true", "yes", "on"}:
                lint_result = subprocess.run(
                    ['ruff', 'check', file_path],
                    capture_output=True,
                    text=True,
                )
                if lint_result.returncode != 0:
                    detail = (lint_result.stderr or lint_result.stdout or "").strip()
                    print(f"Ruff lint check failed: {detail}", file=sys.stderr)
                    return False

            # 默认禁用自动格式化，避免多步 precision_edit 时第 1 步改写文本风格，
            # 导致后续 search 片段失配（例如单引号被格式化为双引号）。
            auto_format = os.environ.get("HP_PRECISION_AUTOFORMAT", "").strip().lower()
            if auto_format in {"1", "true", "yes", "on"}:
                format_result = subprocess.run(
                    ['ruff', 'format', file_path],
                    capture_output=True,
                    text=True,
                )
                if format_result.returncode != 0:
                    detail = (format_result.stderr or format_result.stdout or "").strip()
                    print(f"Ruff format failed: {detail}", file=sys.stderr)
                    return False
            
            return True
            
        except FileNotFoundError:
            print("Ruff not installed, skipping lint gates", file=sys.stderr)
            return True  # 工具不存在时跳过
        except Exception as e:
            print(f"Quality gate error: {e}", file=sys.stderr)
            return False
    
    def _run_js_quality_gates(self, file_path: str) -> bool:
        """JavaScript/TypeScript 质量门禁"""
        try:
            # eslint 检查
            result = subprocess.run(['npx', 'eslint', file_path], 
                                  capture_output=True, text=True)
            if result.returncode != 0:
                print(f"ESLint check failed: {result.stderr}", file=sys.stderr)
                return False

            auto_format = os.environ.get("HP_PRECISION_AUTOFORMAT", "").strip().lower()
            if auto_format in {"1", "true", "yes", "on"}:
                # prettier 格式化
                result = subprocess.run(
                    ['npx', 'prettier', '--write', file_path],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    print(f"Prettier format failed: {result.stderr}", file=sys.stderr)
                    return False
            
            return True
            
        except FileNotFoundError:
            print("ESLint/Prettier not installed, skipping quality gates", file=sys.stderr)
            return True
        except Exception as e:
            print(f"Quality gate error: {e}", file=sys.stderr)
            return False
    
    def _create_backup(self, file_path: str) -> Path:
        """创建备份"""
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = Path(file_path).name
        backup_path = self.backup_dir / f"{timestamp}_{filename}"
        
        with open(file_path, 'r', encoding='utf-8') as src:
            with open(backup_path, 'w', encoding='utf-8') as dst:
                dst.write(src.read())
        
        return backup_path
    
    def _restore_backup(self, file_path: str, backup_path: Path):
        """恢复备份"""
        with open(backup_path, 'r', encoding='utf-8') as src:
            with open(file_path, 'w', encoding='utf-8') as dst:
                dst.write(src.read())


# 全局精确编辑器实例
precision_editor = PrecisionEditor()

# 导出的安全接口
def safe_precision_replace(file_path: str, old_pattern: str, new_text: str, 
                         change_type: str = "general") -> Tuple[bool, str]:
    """安全精确替换的统一接口"""
    
    # 检查是否应该使用字符串替换
    if not precision_editor.should_use_string_replacement(file_path, change_type):
        return False, "Change type not allowed for string replacement, use AST instead"
    
    return precision_editor.precision_replace(file_path, old_pattern, new_text)

if __name__ == "__main__":
    # 测试精确编辑器
    print("🔧 Testing precision editor...")
    
    # 创建测试文件
    test_file = "test_precision.py"
    with open(test_file, 'w', encoding='utf-8') as f:
        f.write("""
# Test file for precision editing
import os

def hello_world():
    print("Hello, World!")
    return True

x = 42
""")
    
    # 测试替换
    success, message = safe_precision_replace(
        test_file, 
        'x = 42', 
        'x = 100',
        'simple_variable_rename'
    )
    
    print(f"Result: {success}, Message: {message}")
    
    # 清理
    if os.path.exists(test_file):
        os.remove(test_file)
