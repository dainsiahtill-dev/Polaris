"""code_audit methods for StressEngine (mixin)."""

# mypy: ignore-errors

import ast
import re
from pathlib import Path

from ._constants import (
    IGNORED_WORKSPACE_ROOTS,
    JS_TS_EMPTY_FUNCTION_PATTERN,
    PROJECT_CODE_EXTENSIONS,
    PYTHON_EMPTY_FUNCTION_FALLBACK_PATTERN,
)


class _StressEngineCodeAuditMixin:
    @staticmethod
    def _is_python_docstring_stmt(node: ast.stmt) -> bool:
        if not isinstance(node, ast.Expr):
            return False
        value = node.value
        if isinstance(value, ast.Constant):
            return isinstance(value.value, str)
        legacy_str_node = getattr(ast, "Str", None)
        return bool(legacy_str_node) and isinstance(value, legacy_str_node)

    @staticmethod
    def _ast_expr_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return str(node.id or "")
        if isinstance(node, ast.Attribute):
            parent = _StressEngineCodeAuditMixin._ast_expr_name(node.value)
            return f"{parent}.{node.attr}" if parent else str(node.attr or "")
        if isinstance(node, ast.Subscript):
            return _StressEngineCodeAuditMixin._ast_expr_name(node.value)
        if isinstance(node, ast.Call):
            return _StressEngineCodeAuditMixin._ast_expr_name(node.func)
        return ""

    @classmethod
    def _is_protocol_or_abstract_function(
        cls,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        parent_map: dict[ast.AST, ast.AST],
    ) -> bool:
        for decorator in node.decorator_list:
            decorator_name = cls._ast_expr_name(decorator)
            if decorator_name.endswith("abstractmethod"):
                return True

        parent = parent_map.get(node)
        if isinstance(parent, ast.ClassDef):
            for base in parent.bases:
                base_name = cls._ast_expr_name(base)
                if base_name.endswith("Protocol") or base_name.endswith("ABC"):
                    return True
        return False

    @classmethod
    def _extract_empty_python_functions(cls, content: str) -> list[str]:
        """Extract Python function names whose bodies are effectively empty."""
        try:
            module = ast.parse(content)
        except SyntaxError:
            return []

        parent_map: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(module):
            for child in ast.iter_child_nodes(parent):
                parent_map[child] = parent

        matches: list[str] = []
        for node in ast.walk(module):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if cls._is_protocol_or_abstract_function(node, parent_map):
                continue
            body = list(node.body or [])
            if body and cls._is_python_docstring_stmt(body[0]):
                body = body[1:]
            if not body:
                matches.append(f"{node.name}(docstring_only)")
                continue
            if len(body) != 1:
                continue
            stmt = body[0]
            if isinstance(stmt, ast.Pass):
                matches.append(f"{node.name}(pass)")
                continue
            if isinstance(stmt, ast.Expr):
                value = stmt.value
                legacy_ellipsis_node = getattr(ast, "Ellipsis", None)
                is_legacy_ellipsis = bool(legacy_ellipsis_node) and isinstance(value, legacy_ellipsis_node)
                if (isinstance(value, ast.Constant) and value.value is Ellipsis) or is_legacy_ellipsis:
                    matches.append(f"{node.name}(ellipsis)")

        # 去重并保持顺序，避免重复命中同一个函数。
        return list(dict.fromkeys(matches))

    @classmethod
    def _extract_empty_function_matches(cls, content: str, suffix: str) -> list[str]:
        normalized_suffix = str(suffix or "").strip().lower()
        if normalized_suffix == ".py":
            python_matches = cls._extract_empty_python_functions(content)
            if python_matches:
                return python_matches
            return [
                (match.group("name") or "").strip()
                for match in PYTHON_EMPTY_FUNCTION_FALLBACK_PATTERN.finditer(content)
                if (match.group("name") or "").strip()
            ]
        if normalized_suffix in {".js", ".jsx", ".ts", ".tsx"}:
            return [match.group(0).strip() for match in JS_TS_EMPTY_FUNCTION_PATTERN.finditer(content)]
        return []

    def _post_batch_code_audit(self, projects: list, sample_size: int = 3, seed: int | None = None) -> dict:
        """批后随机抽查审计

        固定随机种子（可复现）
        随机抽取 N 个项目，每个项目随机抽取 M 个代码文件
        检查：模板占位、重复代码、TODO/FIXME、未完成函数

        Args:
            projects: 项目列表 (RoundResult 列表)
            sample_size: 随机抽查的项目数量
            seed: 随机种子，用于可复现审计

        Returns:
            dict: 审计结果
            {
                "sample_audits": [...],
                "failed_rules_hit": [...],
                "evidence_paths": [...],
            }
        """
        import random

        rng = random.Random(seed)

        # 随机抽取样本
        sampled_projects = rng.sample(projects, min(sample_size, len(projects)))

        sample_audits = []
        failed_rules_hit = []
        evidence_paths = []

        # 定义审计规则
        audit_rules = {
            "todo_fixme": {
                "pattern": re.compile(r"\b(TODO|FIXME|TBD)\b", re.IGNORECASE),
                "severity": "high",
            },
            "not_implemented": {
                "pattern": re.compile(r"\bNotImplemented(?:Error|Exception)?\b", re.IGNORECASE),
                "severity": "high",
            },
            "stub_placeholder": {
                "pattern": re.compile(r"\b(stub|placeholder|实现核心业务逻辑|核心逻辑待实现)\b", re.IGNORECASE),
                "severity": "medium",
            },
            "empty_function": {
                "severity": "medium",
            },
            "generic_scaffold": {
                "patterns": [
                    "项目主入口模块",
                    "通用工具函数模块",
                    "helpers 模块的单元测试",
                    "def safe_divide(",
                    "def parse_arguments(",
                    "应用程序主入口点",
                ],
                "severity": "high",
            },
        }

        for project_result in sampled_projects:
            project_workspace = (
                project_result.workspace_artifacts.get("workspace")
                if isinstance(project_result.workspace_artifacts, dict)
                else None
            )

            if not project_workspace:
                continue

            workspace_path = Path(project_workspace)
            if not workspace_path.exists():
                continue

            # 收集代码文件
            code_files = []
            try:
                for path in workspace_path.rglob("*"):
                    if not path.is_file():
                        continue
                    rel = path.relative_to(workspace_path)
                    if rel.parts and rel.parts[0] in IGNORED_WORKSPACE_ROOTS:
                        continue
                    if path.suffix.lower() in PROJECT_CODE_EXTENSIONS:
                        code_files.append(path)
            except (OSError, PermissionError):
                continue

            if not code_files:
                continue

            # 随机抽取 M 个代码文件
            max_files_per_project = min(5, len(code_files))
            sampled_files = rng.sample(code_files, max_files_per_project)

            project_audit = {
                "project_id": project_result.project.id,
                "project_name": project_result.project.name,
                "files_audited": len(sampled_files),
                "violations": [],
            }

            for code_file in sampled_files:
                try:
                    content = code_file.read_text(encoding="utf-8")
                except (OSError, PermissionError, UnicodeDecodeError):
                    continue

                rel_path = code_file.relative_to(workspace_path)
                evidence_paths.append(str(code_file))

                # 检查各规则
                for rule_name, rule_def in audit_rules.items():
                    violations_found = []

                    if rule_name == "empty_function":
                        empty_matches = self._extract_empty_function_matches(content, code_file.suffix)
                        if empty_matches:
                            violations_found.append(
                                {
                                    "rule": rule_name,
                                    "matches": empty_matches[:5],
                                    "severity": rule_def["severity"],
                                }
                            )

                    if "pattern" in rule_def:
                        matches = rule_def["pattern"].findall(content)
                        if matches:
                            violations_found.append(
                                {
                                    "rule": rule_name,
                                    "matches": matches[:5],  # 限制匹配数量
                                    "severity": rule_def["severity"],
                                }
                            )

                    if rule_name == "generic_scaffold":
                        for marker in rule_def["patterns"]:
                            if marker.lower() in content.lower():
                                violations_found.append(
                                    {
                                        "rule": rule_name,
                                        "marker": marker,
                                        "severity": rule_def["severity"],
                                    }
                                )

                    if violations_found:
                        project_audit["violations"].append(
                            {
                                "file": str(rel_path),
                                "violations": violations_found,
                            }
                        )

                        # 记录失败的规则
                        for v in violations_found:
                            rule_id = f"{project_result.project.id}:{rel_path}:{v['rule']}"
                            if rule_id not in [r.get("rule_id") for r in failed_rules_hit]:
                                failed_rules_hit.append(
                                    {
                                        "rule_id": rule_id,
                                        "project_id": project_result.project.id,
                                        "file": str(rel_path),
                                        "rule": v["rule"],
                                        "severity": v["severity"],
                                    }
                                )

            sample_audits.append(project_audit)

        return {
            "sample_audits": sample_audits,
            "failed_rules_hit": failed_rules_hit,
            "evidence_paths": evidence_paths,
            "audit_metadata": {
                "sample_size": len(sampled_projects),
                "total_projects_audited": len(sample_audits),
                "seed": seed,
            },
        }
