"""Evidence bundle entities for domain layer."""

from __future__ import annotations

__all__ = [
    "BundleComparison",
    "ChangeType",
    "EvidenceBundle",
    "FileChange",
    "PerfEvidence",
    "SourceType",
    "StaticAnalysisEvidence",
    "TestRunEvidence",
]

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar

from polaris.kernelone.utils.time_utils import utc_now


class SourceType(Enum):
    """证据来源类型"""

    DIRECTOR_RUN = "director_run"
    MANUAL = "manual"
    EXPERIMENT = "experiment"
    REVIEW = "review"


class ChangeType(Enum):
    """文件变更类型"""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


@dataclass(frozen=True)
class FileChange:
    """单个文件变更"""

    path: str
    change_type: ChangeType
    before_sha: str | None = None
    after_sha: str | None = None
    patch: str | None = None
    patch_ref: str | None = None
    language: str | None = None
    lines_added: int = 0
    lines_deleted: int = 0
    related_symbols: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "change_type": self.change_type.value,
            "before_sha": self.before_sha,
            "after_sha": self.after_sha,
            "patch": self.patch,
            "patch_ref": self.patch_ref,
            "language": self.language,
            "lines_added": self.lines_added,
            "lines_deleted": self.lines_deleted,
            "related_symbols": self.related_symbols,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileChange:
        return cls(
            path=data["path"],
            change_type=ChangeType(data["change_type"]),
            before_sha=data.get("before_sha"),
            after_sha=data.get("after_sha"),
            patch=data.get("patch"),
            patch_ref=data.get("patch_ref"),
            language=data.get("language"),
            lines_added=data.get("lines_added", 0),
            lines_deleted=data.get("lines_deleted", 0),
            related_symbols=data.get("related_symbols", []),
        )

    @property
    def is_large_patch(self) -> bool:
        """判断是否需要外置存储"""
        if self.patch is None:
            return False
        # 100KB 阈值
        return len(self.patch.encode("utf-8")) >= 100 * 1024


@dataclass(frozen=True)
class TestRunEvidence:
    """测试运行结果证据"""

    __test__: ClassVar[bool] = False

    test_command: str
    exit_code: int
    total_tests: int
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    failed_tests: list[str] = field(default_factory=list)
    coverage_percent: float | None = None
    raw_output_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_command": self.test_command,
            "exit_code": self.exit_code,
            "total_tests": self.total_tests,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "duration_seconds": self.duration_seconds,
            "failed_tests": self.failed_tests,
            "coverage_percent": self.coverage_percent,
            "raw_output_ref": self.raw_output_ref,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestRunEvidence:
        return cls(**data)


@dataclass(frozen=True)
class PerfEvidence:
    """性能证据"""

    benchmark_command: str | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    baseline_comparison: dict[str, float] | None = None
    flamegraph_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_command": self.benchmark_command,
            "metrics": self.metrics,
            "baseline_comparison": self.baseline_comparison,
            "flamegraph_ref": self.flamegraph_ref,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerfEvidence:
        return cls(
            benchmark_command=data.get("benchmark_command"),
            metrics=data.get("metrics", {}),
            baseline_comparison=data.get("baseline_comparison"),
            flamegraph_ref=data.get("flamegraph_ref"),
        )


@dataclass(frozen=True)
class StaticAnalysisEvidence:
    """静态分析证据"""

    tool_name: str
    issues: list[dict[str, Any]] = field(default_factory=list)
    issue_count_by_severity: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "issues": self.issues,
            "issue_count_by_severity": self.issue_count_by_severity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StaticAnalysisEvidence:
        return cls(
            tool_name=data["tool_name"],
            issues=data.get("issues", []),
            issue_count_by_severity=data.get("issue_count_by_severity", {}),
        )


@dataclass(frozen=True)
class EvidenceBundle:
    """统一变更证据包"""

    bundle_id: str
    workspace: str
    base_sha: str
    change_set: list[FileChange]

    created_at: datetime = field(default_factory=utc_now)
    head_sha: str | None = None
    working_tree_dirty: bool = True

    test_results: TestRunEvidence | None = None
    performance_snapshot: PerfEvidence | None = None
    static_analysis: StaticAnalysisEvidence | None = None

    source_type: SourceType = SourceType.MANUAL
    source_run_id: str | None = None
    source_task_id: str | None = None
    source_goal_id: str | None = None

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "bundle_id": self.bundle_id,
            "created_at": self.created_at.isoformat(),
            "workspace": self.workspace,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "working_tree_dirty": self.working_tree_dirty,
            "change_set": [c.to_dict() for c in self.change_set],
            "test_results": self.test_results.to_dict() if self.test_results else None,
            "performance_snapshot": self.performance_snapshot.to_dict() if self.performance_snapshot else None,
            "static_analysis": self.static_analysis.to_dict() if self.static_analysis else None,
            "source_type": self.source_type.value,
            "source_run_id": self.source_run_id,
            "source_task_id": self.source_task_id,
            "source_goal_id": self.source_goal_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceBundle:
        """从字典反序列化"""
        return cls(
            bundle_id=data["bundle_id"],
            workspace=data["workspace"],
            base_sha=data["base_sha"],
            head_sha=data.get("head_sha"),
            working_tree_dirty=data.get("working_tree_dirty", True),
            change_set=[FileChange.from_dict(c) for c in data.get("change_set", [])],
            created_at=datetime.fromisoformat(data["created_at"]),
            test_results=TestRunEvidence.from_dict(data["test_results"]) if data.get("test_results") else None,
            performance_snapshot=PerfEvidence.from_dict(data["performance_snapshot"])
            if data.get("performance_snapshot")
            else None,
            static_analysis=StaticAnalysisEvidence.from_dict(data["static_analysis"])
            if data.get("static_analysis")
            else None,
            source_type=SourceType(data.get("source_type", "manual")),
            source_run_id=data.get("source_run_id"),
            source_task_id=data.get("source_task_id"),
            source_goal_id=data.get("source_goal_id"),
            metadata=data.get("metadata", {}),
        )

    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> EvidenceBundle:
        """从 JSON 字符串反序列化"""
        return cls.from_dict(json.loads(json_str))

    @property
    def total_lines_changed(self) -> tuple[int, int]:
        """返回 (新增行数, 删除行数) 总计"""
        added = sum(c.lines_added for c in self.change_set)
        deleted = sum(c.lines_deleted for c in self.change_set)
        return added, deleted

    @property
    def affected_files(self) -> list[str]:
        """返回所有影响的文件路径列表"""
        return [c.path for c in self.change_set]

    @property
    def affected_symbols(self) -> list[str]:
        """返回所有涉及的符号（去重）"""
        symbols = set()
        for change in self.change_set:
            symbols.update(change.related_symbols)
        return list(symbols)

    def get_change_for_file(self, path: str) -> FileChange | None:
        """获取指定路径的变更"""
        for change in self.change_set:
            if change.path == path:
                return change
        return None

    def compute_content_hash(self) -> str:
        """计算证据包内容哈希（用于去重和比较）"""
        content = f"{self.base_sha}:{self.head_sha}:{sorted(self.affected_files)}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]


# 便捷类型别名
BundleComparison = dict[str, Any]
