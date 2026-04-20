"""Quality Checker - 质量检查组件

负责检查角色输出质量，包括：
- 各角色特定的质量检查
- 质量评分计算
- 输出验证
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.kernelone.security.dangerous_patterns import (
    is_dangerous_command,
    is_path_traversal,
)

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile


@dataclass
class QualityResult:
    """质量检查结果"""

    success: bool
    errors: list[str]
    suggestions: list[str]
    data: dict[str, Any] | None
    quality_score: float
    quality_passed: bool


class QualityChecker:
    """质量检查器

    将质量检查逻辑从RoleExecutionKernel中提取出来，实现单一职责。
    """

    # 模糊词汇（降低质量分数）
    VAGUE_WORDS = ["适当的", "合适的", "根据需要", "等等"]

    # 技术债务标记
    DEBT_MARKERS = ["临时方案", "hack", "TODO", "FIXME"]

    # Default quality threshold
    _DEFAULT_QUALITY_THRESHOLD = 60.0

    def __init__(self, workspace: str = "") -> None:
        self.workspace = workspace
        # Quality threshold from environment variable (POLARIS_QUALITY_THRESHOLD)
        self._quality_threshold = self._resolve_quality_threshold()

    @classmethod
    def _resolve_quality_threshold(cls) -> float:
        """Resolve quality threshold from environment variable."""
        env_value = os.environ.get("POLARIS_QUALITY_THRESHOLD", "").strip()
        if env_value:
            try:
                threshold = float(env_value)
                # Clamp to valid range [0, 100]
                return max(0.0, min(100.0, threshold))
            except (ValueError, TypeError):
                pass
        return cls._DEFAULT_QUALITY_THRESHOLD

    @property
    def quality_threshold(self) -> float:
        """Get the configured quality threshold."""
        return self._quality_threshold

    def set_quality_threshold(self, threshold: float) -> None:
        """Set quality threshold (for testing or runtime adjustment)."""
        self._quality_threshold = max(0.0, min(100.0, threshold))

    def validate_output(
        self,
        content: str,
        profile: RoleProfile,
        pre_validated_data: dict | None = None,
        instructor_validated: bool = False,
    ) -> QualityResult:
        """验证角色输出

        根据角色类型执行相应的验证逻辑。

        Args:
            content: 输出内容
            profile: 角色配置
            pre_validated_data: 预验证数据（来自Instructor）
            instructor_validated: 是否已由Instructor验证

        Returns:
            验证结果
        """
        role = profile.role_id

        # 检查安全违规标记
        if "该请求超出我的职责范围或违反安全策略" in content:
            return QualityResult(
                success=True,
                errors=[],
                suggestions=[],
                data={"security_blocked": True},
                quality_score=100.0,
                quality_passed=True,
            )

        # 如果已由Instructor验证，快速通过基础验证
        if instructor_validated and pre_validated_data is not None:
            # 仍然执行质量检查，但跳过解析
            quality_score, quality_suggestions = self._check_quality(role, content, pre_validated_data)
            return QualityResult(
                success=True,
                errors=[],
                suggestions=quality_suggestions,
                data=pre_validated_data,
                quality_score=quality_score,
                quality_passed=quality_score >= self._quality_threshold,
            )

        errors = []
        suggestions = []
        data = None

        # 根据角色类型选择验证方式
        if role in ["pm", "chief_engineer", "qa"]:
            data, parse_errors = self._extract_json(content)
            if data is None:
                errors.extend(parse_errors)
                score, quality_suggestions = self._check_quality(role, content, None)
                suggestions.extend(quality_suggestions)
                return QualityResult(
                    success=False,
                    errors=errors,
                    suggestions=suggestions,
                    data=None,
                    quality_score=score,
                    quality_passed=False,
                )

        elif role == "director":
            data, errors = self._validate_director_output(content)

        elif role == "architect":
            data, errors = self._validate_architect_output(content)

        else:
            data = {"text": content}

        # 质量评分
        quality_score, quality_suggestions = self._check_quality(role, content, data)
        suggestions.extend(quality_suggestions)

        # 构建建议
        if errors:
            suggestions.append("请严格按照输出格式要求生成")
            if "json" in str(errors).lower():
                suggestions.append("确保JSON格式正确，使用双引号")
            if role == "director" and "补丁" in str(errors):
                suggestions.append("使用正确的SEARCH/REPLACE格式")

        success = len(errors) == 0 and quality_score >= self._quality_threshold

        return QualityResult(
            success=success,
            errors=errors,
            suggestions=suggestions,
            data=data,
            quality_score=quality_score,
            quality_passed=quality_score >= self._quality_threshold,
        )

    def _check_quality(self, role: str, text: str, data: dict[str, Any] | None) -> tuple[float, list[str]]:
        """检查输出质量

        Returns:
            (分数0-100, 建议列表)
        """
        checkers = {
            "pm": self._check_pm_quality,
            "architect": self._check_architect_quality,
            "chief_engineer": self._check_ce_quality,
            "director": self._check_director_quality,
            "qa": self._check_qa_quality,
        }

        checker = checkers.get(role)
        if checker:
            normalized_data = data if isinstance(data, dict) else None
            return checker(text, normalized_data)

        return 100.0, []

    def _check_pm_quality(self, text: str, data: dict[str, Any] | None) -> tuple[float, list[str]]:
        """PM输出质量检查"""
        score = 100.0
        suggestions = []

        if not data:
            return 0, ["Failed to parse output"]

        tasks = data.get("tasks", [])
        if not tasks:
            score -= 50
            suggestions.append("No tasks generated")
        else:
            if len(tasks) > 20:
                score -= 10
                suggestions.append("Too many tasks, consider splitting")

            for task in tasks:
                criteria = task.get("acceptance_criteria", [])
                if not criteria:
                    score -= 5
                    suggestions.append(f"Task {task.get('id')} missing acceptance criteria")

                files = task.get("target_files", [])
                for f in files:
                    if ".." in f or f.startswith("/"):
                        score -= 10
                        suggestions.append(f"Unsafe path in task {task.get('id')}: {f}")

        # 模糊词汇检查
        for word in self.VAGUE_WORDS:
            if word in text:
                score -= 3
                suggestions.append(f"Avoid vague word: {word}")

        return max(0, score), suggestions

    def _check_architect_quality(self, text: str, data: dict[str, Any] | None) -> tuple[float, list[str]]:
        """Architect输出质量检查"""
        score = 100.0
        suggestions = []

        required_sections = ["架构", "技术栈", "模块"]
        for section in required_sections:
            if section not in text:
                score -= 15
                suggestions.append(f"Missing section: {section}")

        for marker in self.DEBT_MARKERS:
            if marker.lower() in text.lower():
                suggestions.append(f"Warning: found debt marker '{marker}'")

        return max(0, score), suggestions

    def _check_ce_quality(self, text: str, data: dict[str, Any] | None) -> tuple[float, list[str]]:
        """Chief Engineer输出质量检查"""
        score = 100.0
        suggestions = []

        if not data:
            return 0, ["Failed to parse output"]

        plan = data.get("construction_plan", {})
        if not plan:
            score -= 30
            suggestions.append("Missing construction_plan")

        scope = data.get("scope_for_apply", [])
        if not scope:
            score -= 20
            suggestions.append("Missing scope_for_apply")

        risks = data.get("risk_flags", [])
        if not risks:
            suggestions.append("No risk assessment provided")

        return max(0, score), suggestions

    def _check_director_quality(self, text: str, data: dict[str, Any] | None) -> tuple[float, list[str]]:
        """Director输出质量检查"""
        score = 100.0
        suggestions: list[str] = []

        has_tool_calls = bool(
            data and isinstance(data.get("tool_calls"), list) and len(data.get("tool_calls") or []) > 0
        )
        has_patch_operations = bool(
            data and isinstance(data.get("patches"), list) and len(data.get("patches") or []) > 0
        )

        # 检查补丁格式（优先信任解析后的补丁操作，避免仅靠字符串关键字导致误判）。
        if not has_patch_operations and "PATCH_FILE:" not in text and "<<<<<<< SEARCH" not in text:
            if has_tool_calls:
                return score, suggestions
            score -= 50
            suggestions.append("No valid patch format found")
        else:
            search_count = text.count("<<<<<<< SEARCH")
            replace_count = text.count(">>>>>>> REPLACE")
            if search_count != replace_count:
                score -= 30
                suggestions.append("Mismatched SEARCH/REPLACE blocks")

        # 安全检查 - 使用 canonical 源头
        if is_path_traversal(text):
            score -= 50
            suggestions.append("Dangerous pattern found: path traversal")
        if is_dangerous_command(text):
            score -= 50
            suggestions.append("Dangerous pattern found: dangerous command")

        return max(0, score), suggestions

    def _check_qa_quality(self, text: str, data: dict[str, Any] | None) -> tuple[float, list[str]]:
        """QA输出质量检查"""
        score = 100.0
        suggestions = []

        if not data:
            return 0, ["Failed to parse output"]

        verdict = data.get("verdict")
        if not verdict:
            score -= 30
            suggestions.append("Missing verdict")
        elif verdict not in ["PASS", "CONDITIONAL", "FAIL", "BLOCKED"]:
            score -= 15
            suggestions.append(f"Invalid verdict: {verdict}")

        findings = data.get("findings", [])
        if verdict == "FAIL" and not findings:
            score -= 30
            suggestions.append("FAIL verdict without findings")

        return max(0, score), suggestions

    def _extract_json(self, text: str) -> tuple[dict | None, list[str]]:
        """提取JSON内容"""
        import json

        errors = []

        if not text or not text.strip():
            return None, ["Empty text"]

        # 尝试匹配 ```json ... ``` 代码块
        json_pattern = r"```(?:json)?\s*(.*?)\s*```"
        matches = re.findall(json_pattern, text, re.DOTALL)

        for match in matches:
            try:
                return json.loads(match.strip()), []
            except json.JSONDecodeError as e:
                errors.append(f"JSON解析错误: {e}")

        return None, errors

    def _validate_director_output(self, content: str) -> tuple[dict | None, list[str]]:
        """验证Director输出"""
        errors = []

        # 尝试提取JSON
        data, _json_errors = self._extract_json(content)

        # 检查补丁
        patches = self._extract_director_patches(content)
        if patches:
            if data is None:
                data = {}
            data["patches"] = patches

        tool_calls = self._extract_tool_calls(content)
        if tool_calls:
            if data is None:
                data = {}
            data["tool_calls"] = tool_calls

        if data is None and not patches and not tool_calls:
            errors.append("未找到有效的JSON或补丁")

        return data, errors

    def _extract_director_patches(self, content: str) -> list[dict[str, str]]:
        """提取 Director 可执行补丁，兼容多种 PATCH 方言。"""
        patches: list[dict[str, str]] = []
        patches.extend(self._extract_patch_operations_from_unified_parser(content))
        if not patches:
            legacy = self._extract_search_replace(content)
            if legacy:
                patches.extend(legacy)
        patches.extend(self._extract_markdown_file_blocks(content))
        return patches

    @staticmethod
    def _extract_patch_operations_from_unified_parser(content: str) -> list[dict[str, str]]:
        try:
            from polaris.cells.director.execution.public.service import parse_all_operations
        except (RuntimeError, ValueError):
            return []

        patches: list[dict[str, str]] = []
        operations = parse_all_operations(str(content or ""))
        for operation in operations:
            path = str(getattr(operation, "path", "") or "").strip()
            if not path:
                continue
            if not QualityChecker._is_safe_relative_path(path):
                continue
            if not QualityChecker._looks_like_patch_target(path):
                continue
            patches.append(
                {
                    "file": path,
                    "search": str(getattr(operation, "search", "") or ""),
                    "replace": str(getattr(operation, "replace", "") or ""),
                }
            )
        return patches

    @staticmethod
    def _extract_markdown_file_blocks(content: str) -> list[dict[str, str]]:
        """提取 Markdown 形式的“文件名 + 代码块”输出。"""
        if not content:
            return []
        pattern = re.compile(
            r"(?:^|\n)(?:#{1,6}\s*|[-*]\s*|)\s*([a-zA-Z0-9_./-]+\.[a-zA-Z0-9]+)\s*\n```[a-zA-Z0-9_-]*\n(.*?)\n```",
            re.DOTALL,
        )
        patches: list[dict[str, str]] = []
        for match in pattern.finditer(content):
            file_path = str(match.group(1) or "").strip()
            replace = str(match.group(2) or "")
            if not file_path or not QualityChecker._is_safe_relative_path(file_path):
                continue
            if not QualityChecker._looks_like_patch_target(file_path):
                continue
            patches.append(
                {
                    "file": file_path,
                    "search": "",
                    "replace": replace,
                }
            )
        return patches

    @staticmethod
    def _is_safe_relative_path(path: str) -> bool:
        token = str(path or "").strip().replace("\\", "/")
        if not token:
            return False
        if token.startswith("/") or token.startswith("\\"):
            return False
        if re.match(r"^[a-zA-Z]:[/\\]", token):
            return False
        if "\x00" in token:
            return False
        parts = [part for part in token.split("/") if part]
        return not any(part in {".", ".."} for part in parts)

    @staticmethod
    def _looks_like_patch_target(path: str) -> bool:
        """Best-effort file-target heuristic to suppress false-positive paths.

        Unified parsers may occasionally mis-detect plain words (for example
        `pass`) as patch paths. We keep legitimate no-extension filenames while
        rejecting generic tokens.
        """
        token = str(path or "").strip().replace("\\", "/")
        if not token:
            return False
        if "/" in token:
            return True
        if "." in token:
            return True

        base = token
        known_no_ext = {
            "Dockerfile",
            "Makefile",
            "README",
            "LICENSE",
            "NOTICE",
            "Jenkinsfile",
            "Procfile",
            "Gemfile",
            "Rakefile",
            "Vagrantfile",
        }
        if base in known_no_ext:
            return True
        return bool(base.lower().startswith("readme"))

    @staticmethod
    def _extract_tool_calls(content: str) -> list[dict[str, Any]]:
        """提取文本包装器中的工具调用，用于质量审计而非执行。"""
        try:
            from polaris.cells.roles.kernel.internal.tool_call_protocol import (
                CanonicalToolCallParser,
            )
        except (RuntimeError, ValueError):
            return []

        calls = CanonicalToolCallParser.parse_text_calls(str(content or ""))
        normalized: list[dict[str, Any]] = []
        for item in calls:
            name = str(getattr(item, "tool", "") or "").strip().lower()
            arguments = getattr(item, "args", {})
            if not name or not isinstance(arguments, dict):
                continue
            normalized.append(
                {
                    "name": name,
                    "arguments": arguments,
                }
            )
        return normalized

    def _validate_architect_output(self, content: str) -> tuple[dict | None, list[str]]:
        """验证Architect输出"""
        errors = []

        required_sections = ["架构", "技术栈", "模块"]
        missing = [s for s in required_sections if s not in content]
        if missing:
            errors.append(f"缺少章节: {missing}")

        return {"text": content}, errors

    def _extract_search_replace(self, content: str) -> list[dict[str, str]] | None:
        """提取SEARCH/REPLACE块"""
        pattern = r"<<<<<<< SEARCH\s*(.*?)\s*=======\s*(.*?)\s*>>>>>>> REPLACE"
        matches = re.findall(pattern, content, re.DOTALL)

        if matches:
            return [{"search": s.strip(), "replace": r.strip()} for s, r in matches]

        return None

    def validate_edit_blocks_format(self, content: str) -> tuple[bool, list[str]]:
        """验证 SEARCH/REPLACE 块格式。

        Args:
            content: 包含编辑块的内容

        Returns:
            (是否有效, 错误列表)
        """
        from polaris.kernelone.editing.editblock_engine import (
            parse_edit_blocks,
            validate_edit_blocks,
        )

        errors: list[str] = []

        try:
            blocks = parse_edit_blocks(content)
        except Exception as e:
            return False, [f"解析编辑块失败: {e}"]

        if not blocks:
            # 没有编辑块 - 这不一定是错误，可能只是没有编辑
            return True, []

        # 验证每个块
        validation_errors = validate_edit_blocks(blocks)
        if validation_errors:
            errors.extend(validation_errors)

        # 检查格式问题
        search_pattern = r"<\s*SEARCH"
        replace_pattern = r">\s*REPLACE"

        search_count = len(re.findall(search_pattern, content))
        replace_count = len(re.findall(replace_pattern, content))

        if search_count != replace_count:
            errors.append(f"SEARCH/REPLACE 块不匹配: {search_count} SEARCH vs {replace_count} REPLACE")

        return len(errors) == 0, errors
