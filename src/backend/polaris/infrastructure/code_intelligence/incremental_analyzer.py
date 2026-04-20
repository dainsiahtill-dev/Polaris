"""增量语义分析器 - 只分析变更文件的语义关系.

用于 ChiefEngineer 在已有索引基础上，只分析新增或变更的文件.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from polaris.infrastructure.accel.config import resolve_effective_config
from polaris.infrastructure.accel.indexers import build_or_update_indexes
from polaris.infrastructure.db.adapters import SqliteAdapter
from polaris.kernelone.db import KernelDatabase


@dataclass
class FileChangeInfo:
    """文件变更信息."""

    path: str
    change_type: str  # 'added', 'modified', 'deleted'
    old_checksum: str = ""
    new_checksum: str = ""
    dependencies: list[str] = field(default_factory=list)


class IncrementalSemanticAnalyzer:
    """增量语义分析器.

    只分析变更文件及其直接依赖关系，复用已有索引数据.
    """

    def __init__(self, workspace: str | Path, accel_home: str | Path | None = None) -> None:
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        self.workspace = Path(workspace).resolve()
        metadata_dir = get_workspace_metadata_dir_name()
        self.accel_home = Path(accel_home) if accel_home else self.workspace / metadata_dir
        self._db_path = self.accel_home / "state" / "incremental_analysis.db"
        self._kernel_db = KernelDatabase(
            str(self.workspace),
            sqlite_adapter=SqliteAdapter(),
            allow_unmanaged_absolute=True,
        )
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._config: dict[str, Any] | None = None
        self._init_db()

    def _connect(self):
        return self._kernel_db.sqlite(
            str(self._db_path),
            timeout_seconds=10.0,
            check_same_thread=False,
            ensure_parent=True,
        )

    def _init_db(self) -> None:
        """初始化数据库表."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS known_files (
                    path TEXT PRIMARY KEY,
                    checksum TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.commit()

    def _db_get(self, key: str) -> Any | None:
        """从数据库获取值."""
        try:
            with self._connect() as conn:
                cursor = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    return json.loads(row[0])
        except (RuntimeError, ValueError) as e:
            logger.error("Failed to load value for key=%s: %s", key, e, exc_info=True)
        return None

    def _db_set(self, key: str, value: Any) -> None:
        """设置数据库值."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO kv_store (key, value)
                    VALUES (?, ?)
                    """,
                    (key, json.dumps(value, default=str)),
                )
                conn.commit()
        except (RuntimeError, ValueError) as e:
            logger.error("Failed to set db value for key=%s: %s", key, e, exc_info=True)

    @property
    def config(self) -> dict[str, Any]:
        if self._config is None:
            self._config = resolve_effective_config(self.workspace)
            if "runtime" not in self._config:
                self._config["runtime"] = {}
            self._config["runtime"]["accel_home"] = str(self.accel_home)
        return self._config

    def detect_changes(self, scan_files: list[str]) -> list[FileChangeInfo]:
        """检测文件变更.

        Args:
            scan_files: 当前扫描的文件列表

        Returns:
            变更信息列表
        """
        changes = []
        known_files = self._load_known_files()
        current_files = set()

        for rel_path in scan_files:
            full_path = self.workspace / rel_path
            if not full_path.exists():
                continue

            current_files.add(rel_path)
            new_checksum = self._compute_checksum(full_path)
            old_checksum = known_files.get(rel_path, "")

            if not old_checksum:
                changes.append(FileChangeInfo(path=rel_path, change_type="added", new_checksum=new_checksum))
            elif old_checksum != new_checksum:
                changes.append(
                    FileChangeInfo(
                        path=rel_path, change_type="modified", old_checksum=old_checksum, new_checksum=new_checksum
                    )
                )

        # 检测删除的文件
        for rel_path in known_files:
            if rel_path not in current_files:
                changes.append(FileChangeInfo(path=rel_path, change_type="deleted", old_checksum=known_files[rel_path]))

        return changes

    def analyze_incremental(
        self,
        changes: list[FileChangeInfo],
        task_description: str,
    ) -> dict[str, Any]:
        """增量分析变更文件.

        Args:
            changes: 变更文件列表
            task_description: 任务描述（用于语义相关性）

        Returns:
            增量分析结果
        """
        if not changes:
            return {
                "status": "no_changes",
                "affected_files": [],
                "semantic_relations": {},
                "impact_analysis": {},
            }

        # 1. 只更新变更文件的索引
        changed_paths = [c.path for c in changes if c.change_type != "deleted"]
        if changed_paths:
            self._update_index_for_files(changed_paths)

        # 2. 构建语义关系图（只包含变更文件及其邻居）
        semantic_graph = self._build_partial_graph(changed_paths, task_description)

        # 3. 分析影响范围
        impact = self._analyze_impact(changes, semantic_graph)

        # 4. 保存已知文件状态
        self._save_known_files()

        return {
            "status": "success",
            "changes_analyzed": len(changes),
            "changed_files": [c.path for c in changes],
            "affected_files": impact.get("affected_files", []),
            "semantic_relations": semantic_graph,
            "impact_analysis": impact,
            "recommendations": self._generate_recommendations(impact),
        }

    def _load_known_files(self) -> dict[str, str]:
        """加载已知文件校验和."""
        try:
            with self._connect() as conn:
                cursor = conn.execute("SELECT path, checksum FROM known_files")
                rows = cursor.fetchall()
                return {row[0]: row[1] for row in rows}
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to load known files: {e}")
        return {}

    def _save_known_files(self) -> None:
        """保存当前文件校验和."""
        try:
            with self._connect() as conn:
                # 清空旧数据
                conn.execute("DELETE FROM known_files")
                # 插入新数据
                for f in self.workspace.rglob("*"):
                    if f.is_file() and self._should_index(f):
                        rel_path = str(f.relative_to(self.workspace)).replace("\\", "/")
                        checksum = self._compute_checksum(f)
                        conn.execute("INSERT INTO known_files (path, checksum) VALUES (?, ?)", (rel_path, checksum))
                conn.commit()
        except (RuntimeError, ValueError) as e:
            logger.error("Failed to save known files: %s", e, exc_info=True)

    def close(self) -> None:
        """释放可回收状态，避免长会话保留不必要缓存."""
        self._config = None

    def _should_index(self, path: Path) -> bool:
        """判断文件是否应该被索引."""
        # 排除常见目录
        exclude_dirs = {
            "node_modules",
            ".git",
            "__pycache__",
            ".venv",
            "venv",
            "dist",
            "build",
            ".polaris",
            ".polaris",  # backward compat
            ".accel",
        }
        for part in path.parts:
            if part in exclude_dirs:
                return False

        # 只包含代码文件
        code_extensions = {
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".go",
            ".rs",
            ".java",
            ".kt",
            ".scala",
            ".cpp",
            ".c",
            ".h",
            ".hpp",
        }
        return path.suffix.lower() in code_extensions

    def _compute_checksum(self, path: Path) -> str:
        """计算文件校验和."""
        try:
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()[:16]
        except (RuntimeError, ValueError) as e:
            logger.error("Failed to compute checksum for %s: %s", path, e, exc_info=True)
            return ""

    def _update_index_for_files(self, file_paths: list[str]) -> None:
        """只更新指定文件的索引（增量更新）."""
        # 使用 accel 的增量更新功能
        build_or_update_indexes(
            project_dir=self.workspace,
            config=self.config,
            mode="update",
            full=False,  # 增量更新
        )

    def _build_partial_graph(
        self,
        changed_files: list[str],
        task_description: str,
    ) -> dict[str, Any]:
        """构建局部语义关系图."""
        # 获取变更文件的语义邻居
        neighbors = self._find_semantic_neighbors(changed_files)

        # 构建关系图 - 返回简化的关系结构
        all_files = set(changed_files) | neighbors

        # Note: build_semantic_relation_graph requires pre-computed data
        # Return a simplified graph structure for now
        relations: list[dict[str, Any]] = []
        for src_file in changed_files:
            for neighbor in neighbors:
                relations.append(
                    {
                        "source": src_file,
                        "target": neighbor,
                        "weight": 1.0,
                        "type": "dependency",
                    }
                )

        return {
            "files": list(all_files),
            "relations": relations,
            "query": task_description,
        }

    def _find_semantic_neighbors(self, files: list[str], depth: int = 1) -> set[str]:
        """查找语义邻居文件."""
        neighbors = set()

        # Simple heuristic: find files that might be related based on naming patterns
        for file_path in files:
            # Check for common patterns like imports/dependencies
            full_path = self.workspace / file_path
            if not full_path.exists():
                continue

            # Simple heuristic: look for files in same directory or related directories
            parent_dir = full_path.parent
            for sibling in parent_dir.iterdir():
                if sibling.is_file() and self._should_index(sibling):
                    rel_path = str(sibling.relative_to(self.workspace)).replace("\\", "/")
                    if rel_path not in files:
                        neighbors.add(rel_path)

        return neighbors

    def _analyze_impact(
        self,
        changes: list[FileChangeInfo],
        semantic_graph: dict[str, Any],
    ) -> dict[str, Any]:
        """分析变更影响范围."""
        affected = set()
        critical_paths = []

        for change in changes:
            # 检查是否为关键文件
            if self._is_critical_file(change.path):
                critical_paths.append(change.path)

            # 查找受影响的上游文件
            affected.update(self._find_affected_upstream(change.path, semantic_graph))

        return {
            "affected_files": list(affected),
            "critical_paths": critical_paths,
            "risk_level": self._calculate_risk(changes, affected),
            "test_files": self._find_related_tests([c.path for c in changes]),
        }

    def _is_critical_file(self, path: str) -> bool:
        """判断是否为关键文件."""
        critical_patterns = ["config", "settings", "main", "app", "core", "index", "__init__", "constants", "types"]
        path_lower = path.lower()
        return any(p in path_lower for p in critical_patterns)

    def _find_affected_upstream(
        self,
        file_path: str,
        semantic_graph: dict[str, Any],
    ) -> set[str]:
        """查找受影响的上游文件."""
        affected = set()
        relations = semantic_graph.get("relations", [])

        for rel in relations:
            if rel.get("target") == file_path:
                affected.add(rel.get("source", ""))

        return affected - {file_path, ""}

    def _find_related_tests(self, source_files: list[str]) -> list[str]:
        """查找相关的测试文件."""
        test_files = []

        # Simple heuristic: find test files based on naming conventions
        for src_file in source_files:
            # Try common test file naming patterns
            for ext in [".test.py", "_test.py", ".spec.ts", ".test.ts"]:
                test_path = str(src_file).replace(".py", ext).replace(".ts", ext)
                if (self.workspace / test_path).exists():
                    test_files.append(test_path)

            # Also check for tests in a tests/ directory
            src_name = Path(src_file).stem
            for test_dir in ["tests", "test"]:
                potential_test = f"{test_dir}/test_{src_name}.py"
                if (self.workspace / potential_test).exists():
                    test_files.append(potential_test)

        return list(set(test_files))

    def _calculate_risk(
        self,
        changes: list[FileChangeInfo],
        affected: set[str],
    ) -> str:
        """计算变更风险等级."""
        score = 0

        # 文件数量
        score += len(changes) * 1

        # 影响范围
        score += len(affected) * 2

        # 关键文件
        for change in changes:
            if self._is_critical_file(change.path):
                score += 5

        if score <= 3:
            return "low"
        elif score <= 8:
            return "medium"
        else:
            return "high"

    def _generate_recommendations(self, impact: dict[str, Any]) -> list[str]:
        """生成建议."""
        recommendations = []

        if impact.get("risk_level") == "high":
            recommendations.append("高风险变更，建议仔细审查影响范围")

        if impact.get("critical_paths"):
            recommendations.append(f"关键文件受影响: {', '.join(impact['critical_paths'][:3])}")

        test_files = impact.get("test_files", [])
        if test_files:
            recommendations.append(f"建议运行相关测试: {len(test_files)} 个测试文件")
        else:
            recommendations.append("未找到相关测试，考虑添加测试覆盖")

        return recommendations
