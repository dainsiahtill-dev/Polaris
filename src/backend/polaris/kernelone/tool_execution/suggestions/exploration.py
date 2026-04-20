"""ExplorationBuilder - 文件不存在时的探索建议。

当工具因文件不存在而失败时，提供：
1. 基于 Levenshtein 距离的最接近文件名建议
2. 同目录下文件的列表（如果可用）
3. 建议使用 repo_tree() 或 glob() 探索工作区
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any

from polaris.kernelone.constants import SIMILARITY_THRESHOLD, WORKSPACE_FILES_SMALL_THRESHOLD

if TYPE_CHECKING:
    pass


class ExplorationBuilder:
    """为文件不存在类错误构建探索建议。"""

    name: str = "exploration"
    priority: int = 20

    def should_apply(self, error_result: dict[str, Any]) -> bool:
        error = str(error_result.get("error") or "").strip().lower()
        return "not found" in error or "does not exist" in error or "no such file" in error

    def build(self, error_result: dict[str, Any], **kwargs: Any) -> str | None:
        error = str(error_result.get("error") or "").strip()
        file_path = error_result.get("file") or error_result.get("args", {}).get("file", "")
        workspace_files: list[str] = kwargs.get("workspace_files", [])
        workspace_dirs: list[str] = kwargs.get("workspace_dirs", [])

        parts = []

        # 1. 文件名级别的相似度建议
        if workspace_files and file_path:
            file_name = file_path.split("/")[-1].split("\\")[-1]
            best_file = self._find_similar_name(file_name, workspace_files)
            if best_file and best_file != file_name:
                parts.append(f"Did you mean: {best_file!r}?")

        # 2. 如果 workspace 文件列表很小，给出全部文件
        if workspace_files and len(workspace_files) <= WORKSPACE_FILES_SMALL_THRESHOLD:
            file_list = ", ".join(sorted(workspace_files)[:20])
            parts.append(f"Available files: [{file_list}]" + (" ..." if len(workspace_files) > 20 else ""))

        # 3. 通用探索建议
        if workspace_dirs:
            dir_list = ", ".join(sorted(workspace_dirs)[:10])
            parts.append(f"Try: repo_tree() or glob(pattern='**/*.py') to explore. Directories: [{dir_list}]")
        else:
            parts.append("Use repo_tree() or glob(pattern='**/*') to explore workspace structure.")

        return f"{error}. {' '.join(parts)}"

    @staticmethod
    def _find_similar_name(name: str, candidates: list[str]) -> str | None:
        """Find the most similar name from candidates using Levenshtein-like ratio."""
        if not name or not candidates:
            return None
        name_lower = name.lower()
        best: tuple[float, str] = (0.0, "")
        for candidate in candidates:
            # Compare just the filename part
            cand_name = candidate.split("/")[-1].split("\\")[-1].lower()
            ratio = difflib.SequenceMatcher(None, name_lower, cand_name).ratio()
            if ratio > best[0]:
                best = (ratio, candidate)
        if best[0] >= SIMILARITY_THRESHOLD:
            return best[1]
        return None
