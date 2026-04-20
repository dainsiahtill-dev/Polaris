"""Review Gate - 代码评审质量门禁

管理代码变更的评审流程，确保只有通过评审的代码才能流转到 completed。
与 Director Worker 状态机联动，未通过评审的任务不能标记为 completed。

状态管理策略：
- ReviewGate 无全局单例；每个调用方通过 create_review_gate() 工厂函数
  获取独立实例，或通过依赖注入接收外部实例。
- 测试可直接实例化 ReviewGate() 而不需要任何清理操作。
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any


class ReviewEventType(Enum):
    """评审事件类型"""

    DIFF_GENERATED = auto()  # Diff 已生成
    REVIEW_REQUESTED = auto()  # 评审已请求
    REVIEW_STARTED = auto()  # 评审已开始
    REVIEW_APPROVED = auto()  # 评审通过
    REVIEW_REJECTED = auto()  # 评审拒绝
    REVIEW_COMMENTS = auto()  # 有评审评论


@dataclass
class CodeChange:
    """代码变更"""

    change_id: str
    task_id: str
    worker_id: str
    file_path: str
    base_sha: str
    head_sha: str
    hunks: list[dict[str, Any]] = field(default_factory=list)
    status: str = "pending"  # pending, generated, reviewing, approved, rejected
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "file_path": self.file_path,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "hunks": self.hunks,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class Review:
    """评审记录"""

    review_id: str
    change_id: str
    task_id: str
    worker_id: str
    verdict: str | None = None  # approved, rejected, comments
    reviewer: str | None = None
    comments: list[dict[str, Any]] = field(default_factory=list)
    status: str = "pending"  # pending, reviewing, completed
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "change_id": self.change_id,
            "task_id": self.task_id,
            "worker_id": self.worker_id,
            "verdict": self.verdict,
            "reviewer": self.reviewer,
            "comments": self.comments,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class ReviewGate:
    """评审门禁管理器

    确保代码变更必须经过评审才能完成。
    状态流转: pending -> generated -> reviewing -> approved/rejected

    只有 verdict=approved 时，任务才能流转到 completed。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._code_changes: dict[str, CodeChange] = {}
        self._reviews: dict[str, Review] = {}
        self._task_to_changes: dict[str, list[str]] = {}  # task_id -> [change_id, ...]

    def create_code_change(
        self,
        task_id: str,
        worker_id: str,
        file_path: str,
        base_sha: str,
        head_sha: str,
        hunks: list[dict[str, Any]] | None = None,
    ) -> CodeChange:
        """创建代码变更记录"""
        change_id = f"change-{uuid.uuid4().hex}"
        change = CodeChange(
            change_id=change_id,
            task_id=task_id,
            worker_id=worker_id,
            file_path=file_path,
            base_sha=base_sha,
            head_sha=head_sha,
            hunks=hunks or [],
            status="generated",
        )
        with self._lock:
            self._code_changes[change_id] = change
            self._task_to_changes.setdefault(task_id, []).append(change_id)
        return change

    def request_review(self, change_id: str) -> Review | None:
        """请求评审

        当 Worker 完成任务时调用，触发评审流程。
        """
        review_id = f"review-{uuid.uuid4().hex}"
        with self._lock:
            change = self._code_changes.get(change_id)
            if not change:
                return None

            # 更新变更状态
            change.status = "reviewing"
            change.updated_at = datetime.now()

            # 创建评审记录
            review = Review(
                review_id=review_id,
                change_id=change_id,
                task_id=change.task_id,
                worker_id=change.worker_id,
                status="reviewing",
            )
            self._reviews[review_id] = review

        return review

    def approve_review(self, review_id: str, reviewer: str = "system") -> Review | None:
        """通过评审"""
        with self._lock:
            review = self._reviews.get(review_id)
            if not review:
                return None

            review.verdict = "approved"
            review.reviewer = reviewer
            review.status = "completed"
            review.updated_at = datetime.now()

            # 更新关联的代码变更状态
            change = self._code_changes.get(review.change_id)
            if change:
                change.status = "approved"
                change.updated_at = datetime.now()

        return review

    def reject_review(
        self, review_id: str, reviewer: str = "system", comments: list[dict[str, Any]] | None = None
    ) -> Review | None:
        """拒绝评审

        评审未通过时，任务状态保持 in_progress，Worker 状态回退到 claimed。
        """
        with self._lock:
            review = self._reviews.get(review_id)
            if not review:
                return None

            review.verdict = "rejected"
            review.reviewer = reviewer
            review.comments = comments or []
            review.status = "completed"
            review.updated_at = datetime.now()

            # 更新关联的代码变更状态
            change = self._code_changes.get(review.change_id)
            if change:
                change.status = "rejected"
                change.updated_at = datetime.now()

        return review

    def add_comments(self, review_id: str, comments: list[dict[str, Any]]) -> Review | None:
        """添加评审评论"""
        with self._lock:
            review = self._reviews.get(review_id)
            if not review:
                return None

            review.comments.extend(comments)
            review.updated_at = datetime.now()

        return review

    def can_complete_task(self, task_id: str) -> bool:
        """检查任务是否可以通过（评审必须通过）

        只有当关联的代码变更状态为 approved 时，任务才能标记为 completed。
        """
        with self._lock:
            change_ids = self._task_to_changes.get(task_id, [])
            if not change_ids:
                # 没有代码变更记录，允许完成
                return True

            # 所有变更必须通过评审才能完成任务
            for change_id in change_ids:
                change = self._code_changes.get(change_id)
                if not change:
                    continue
                if change.status != "approved":
                    return False
            return True

    def get_task_review_status(self, task_id: str) -> str | None:
        """获取任务的评审状态"""
        with self._lock:
            change_ids = self._task_to_changes.get(task_id, [])
            if not change_ids:
                return None

            # Return the status of the most recent change
            change_id = change_ids[-1]
            change = self._code_changes.get(change_id)
            return change.status if change else None

    def get_code_change(self, change_id: str) -> CodeChange | None:
        """获取代码变更"""
        with self._lock:
            return self._code_changes.get(change_id)

    def get_review(self, review_id: str) -> Review | None:
        """获取评审记录"""
        with self._lock:
            return self._reviews.get(review_id)

    def get_reviews_by_status(self, status: str) -> list[Review]:
        """按状态获取评审列表"""
        with self._lock:
            return [r for r in self._reviews.values() if r.status == status]

    def get_pending_reviews(self) -> list[Review]:
        """获取待评审列表"""
        return self.get_reviews_by_status("pending")

    def get_all_changes(self) -> list[CodeChange]:
        """获取所有代码变更"""
        with self._lock:
            return list(self._code_changes.values())

    def get_all_reviews(self) -> list[Review]:
        """获取所有评审"""
        with self._lock:
            return list(self._reviews.values())


def create_review_gate() -> ReviewGate:
    """工厂函数：创建一个新的 ReviewGate 实例。

    调用方（应用层、DI 容器）负责管理实例生命周期。
    测试可直接使用 ``ReviewGate()`` 或本函数，无需任何全局清理。

    Returns:
        ReviewGate: 全新的评审门禁实例。
    """
    return ReviewGate()


# Singleton accessor for backward compatibility with callers that expect a
# shared instance (e.g. HTTP routers, cross-task state queries).
# Tests should use ``create_review_gate()`` or ``ReviewGate()`` for isolation.
_review_gate_singleton: ReviewGate | None = None
_review_gate_singleton_lock = threading.Lock()


def get_review_gate() -> ReviewGate:
    """返回全局单例 ReviewGate 实例。

    用于需要共享状态的调用方（如 HTTP 层、跨任务状态保持）。
    测试应优先使用 ``create_review_gate()`` 或 ``ReviewGate()`` 以获得隔离实例。

    Returns:
        ReviewGate: 全局共享的 ReviewGate 单例。
    """
    global _review_gate_singleton
    if _review_gate_singleton is None:
        with _review_gate_singleton_lock:
            if _review_gate_singleton is None:
                _review_gate_singleton = ReviewGate()
    return _review_gate_singleton
