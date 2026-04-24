"""Tests for Goal Execution Projection (Phase 1.2).

验证:
1. 阶段推断算法
2. ETA 估算算法
3. 执行投影构建
"""

import unittest
from datetime import datetime, timedelta

from polaris.cells.resident.autonomy.internal.execution_projection import (
    ExecutionProjectionService,
    TaskProgressItem,
    build_goal_execution_projection,
    calculate_percent,
    estimate_eta,
    get_current_task,
    infer_stage,
)


class TestInferStage(unittest.TestCase):
    """Test stage inference algorithm."""

    def test_empty_tasks_returns_unknown(self):
        """空任务列表返回 unknown"""
        self.assertEqual(infer_stage([]), "unknown")

    def test_all_completed_returns_completed(self):
        """全部完成返回 completed"""
        tasks = [
            TaskProgressItem("t1", "任务1", "completed"),
            TaskProgressItem("t2", "任务2", "completed"),
        ]
        self.assertEqual(infer_stage(tasks), "completed")

    def test_planning_keywords(self):
        """规划阶段关键词匹配"""
        tasks = [
            TaskProgressItem("t1", "设计系统架构", "in_progress"),
            TaskProgressItem("t2", "实现功能", "pending"),
        ]
        self.assertEqual(infer_stage(tasks), "planning")

    def test_coding_keywords(self):
        """编码阶段关键词匹配"""
        tasks = [
            TaskProgressItem("t1", "任务1", "completed"),
            TaskProgressItem("t2", "实现登录功能", "in_progress"),
        ]
        self.assertEqual(infer_stage(tasks), "coding")

    def test_testing_keywords(self):
        """测试阶段关键词匹配"""
        tasks = [
            TaskProgressItem("t1", "任务1", "completed"),
            TaskProgressItem("t2", "运行单元测试", "in_progress"),  # 避免"编写"触发coding
        ]
        self.assertEqual(infer_stage(tasks), "testing")

    def test_review_keywords(self):
        """审查阶段关键词匹配"""
        tasks = [
            TaskProgressItem("t1", "任务1", "completed"),
            TaskProgressItem("t2", "Audit Review", "in_progress"),  # 避免"Code"触发coding
        ]
        self.assertEqual(infer_stage(tasks), "review")

    def test_fallback_to_ratio(self):
        """无关键词匹配时按完成比例推断"""
        # 低完成率 -> planning
        tasks = [
            TaskProgressItem("t1", "x1", "completed"),
            TaskProgressItem("t2", "x2", "pending"),
            TaskProgressItem("t3", "x3", "pending"),
            TaskProgressItem("t4", "x4", "pending"),
            TaskProgressItem("t5", "x5", "pending"),
        ]
        self.assertEqual(infer_stage(tasks), "planning")

        # 中等完成率 -> coding
        tasks = [
            TaskProgressItem("t1", "x1", "completed"),
            TaskProgressItem("t2", "x2", "completed"),
            TaskProgressItem("t3", "x3", "completed"),
            TaskProgressItem("t4", "x4", "pending"),
            TaskProgressItem("t5", "x5", "pending"),
        ]
        self.assertEqual(infer_stage(tasks), "coding")

        # 高完成率 -> testing
        tasks = [
            TaskProgressItem("t1", "x1", "completed"),
            TaskProgressItem("t2", "x2", "completed"),
            TaskProgressItem("t3", "x3", "completed"),
            TaskProgressItem("t4", "x4", "completed"),
            TaskProgressItem("t5", "x5", "pending"),
        ]
        self.assertEqual(infer_stage(tasks), "testing")


class TestCalculatePercent(unittest.TestCase):
    """Test progress percentage calculation."""

    def test_empty_tasks(self):
        """空任务返回 0"""
        self.assertEqual(calculate_percent([]), 0.0)

    def test_all_pending(self):
        """全部待处理"""
        tasks = [
            TaskProgressItem("t1", "任务1", "pending"),
            TaskProgressItem("t2", "任务2", "pending"),
        ]
        self.assertEqual(calculate_percent(tasks), 0.0)

    def test_all_completed(self):
        """全部完成"""
        tasks = [
            TaskProgressItem("t1", "任务1", "completed"),
            TaskProgressItem("t2", "任务2", "completed"),
        ]
        self.assertEqual(calculate_percent(tasks), 1.0)

    def test_in_progress_weight(self):
        """进行中任务使用其细粒度进度"""
        tasks = [
            TaskProgressItem("t1", "任务1", "completed"),
            TaskProgressItem("t2", "任务2", "in_progress", 0.5),
        ]
        self.assertEqual(calculate_percent(tasks), 0.75)

    def test_progress_percent(self):
        """使用细粒度进度"""
        tasks = [
            TaskProgressItem("t1", "任务1", "in_progress", 0.5),
        ]
        self.assertEqual(calculate_percent(tasks), 0.5)


class TestEstimateEta(unittest.TestCase):
    """Test ETA estimation."""

    def test_empty_tasks(self):
        """空任务返回 None"""
        self.assertIsNone(estimate_eta([]))

    def test_all_completed(self):
        """全部完成返回 0"""
        now = datetime.utcnow()
        tasks = [
            TaskProgressItem(
                "t1",
                "任务1",
                "completed",
                1.0,
                started_at=(now - timedelta(minutes=5)).isoformat(),
                completed_at=now.isoformat(),
            ),
        ]
        self.assertEqual(estimate_eta(tasks), 0)

    def test_default_estimate(self):
        """无历史数据使用默认估算"""
        tasks = [
            TaskProgressItem("t1", "任务1", "completed"),  # 无时间信息
            TaskProgressItem("t2", "任务2", "pending"),
            TaskProgressItem("t3", "任务3", "pending"),
        ]
        # 默认每个任务 5 分钟
        eta = estimate_eta(tasks)
        self.assertEqual(eta, 10)  # 2 * 5

    def test_historical_average(self):
        """使用历史平均耗时"""
        now = datetime.utcnow()
        tasks = [
            TaskProgressItem(
                "t1",
                "任务1",
                "completed",
                1.0,
                started_at=(now - timedelta(minutes=10)).isoformat(),
                completed_at=now.isoformat(),
            ),
            TaskProgressItem(
                "t2",
                "任务2",
                "completed",
                1.0,
                started_at=(now - timedelta(minutes=10)).isoformat(),
                completed_at=now.isoformat(),
            ),
            TaskProgressItem("t3", "任务3", "pending"),
        ]
        # 平均 10 分钟，还剩 1 个任务
        eta = estimate_eta(tasks)
        self.assertEqual(eta, 10)

    def test_in_progress_adjustment(self):
        """进行中任务按剩余比例计算"""
        now = datetime.utcnow()
        tasks = [
            TaskProgressItem(
                "t1",
                "任务1",
                "completed",
                1.0,
                started_at=(now - timedelta(minutes=10)).isoformat(),
                completed_at=now.isoformat(),
            ),
            TaskProgressItem(
                "t2",
                "任务2",
                "in_progress",
                0.5,  # 已完成 50%
                started_at=(now - timedelta(minutes=5)).isoformat(),
            ),
        ]
        # 平均 10 分钟，进行中任务剩余 5 分钟
        eta = estimate_eta(tasks)
        self.assertEqual(eta, 5)


class TestGetCurrentTask(unittest.TestCase):
    """Test current task extraction."""

    def test_in_progress_first(self):
        """优先返回进行中任务"""
        tasks = [
            TaskProgressItem("t1", "任务1", "completed"),
            TaskProgressItem("t2", "任务2", "in_progress"),
            TaskProgressItem("t3", "任务3", "pending"),
        ]
        self.assertEqual(get_current_task(tasks), "任务2")

    def test_failed_second(self):
        """其次返回失败任务"""
        tasks = [
            TaskProgressItem("t1", "任务1", "completed"),
            TaskProgressItem("t2", "任务2", "failed"),
            TaskProgressItem("t3", "任务3", "pending"),
        ]
        self.assertEqual(get_current_task(tasks), "[失败] 任务2")

    def test_blocked_third(self):
        """再次返回阻塞任务"""
        tasks = [
            TaskProgressItem("t1", "任务1", "completed"),
            TaskProgressItem("t2", "任务2", "blocked"),
        ]
        self.assertEqual(get_current_task(tasks), "[阻塞] 任务2")

    def test_no_active_tasks(self):
        """无活跃任务返回 None"""
        tasks = [
            TaskProgressItem("t1", "任务1", "completed"),
            TaskProgressItem("t2", "任务2", "completed"),
        ]
        self.assertIsNone(get_current_task(tasks))


class TestBuildGoalExecutionProjection(unittest.TestCase):
    """Test full projection building."""

    def test_complete_build(self):
        """测试完整构建"""
        task_progress = [
            {
                "task_id": "t1",
                "subject": "设计架构",
                "status": "completed",
                "progress_percent": 1.0,
            },
            {
                "task_id": "t2",
                "subject": "实现功能",
                "status": "in_progress",
                "progress_percent": 0.5,
            },
            {
                "task_id": "t3",
                "subject": "编写测试",
                "status": "pending",
                "progress_percent": 0.0,
            },
        ]

        view = build_goal_execution_projection("goal-123", task_progress)

        self.assertEqual(view.goal_id, "goal-123")
        self.assertEqual(view.stage, "coding")
        self.assertEqual(view.total_tasks, 3)
        self.assertEqual(view.completed_tasks, 1)
        self.assertEqual(view.current_task, "实现功能")
        self.assertTrue(0.0 <= view.percent <= 1.0)
        self.assertIsNotNone(view.updated_at)

    def test_to_dict_serialization(self):
        """测试字典序列化"""
        task_progress = [
            {
                "task_id": "t1",
                "subject": "任务1",
                "status": "completed",
            },
        ]

        view = build_goal_execution_projection("goal-123", task_progress)
        data = view.to_dict()

        self.assertEqual(data["goal_id"], "goal-123")
        self.assertEqual(data["stage"], "completed")
        self.assertEqual(data["percent"], 1.0)
        self.assertEqual(data["total_tasks"], 1)
        self.assertEqual(data["completed_tasks"], 1)
        self.assertIn("task_progress", data)


class TestExecutionProjectionService(unittest.TestCase):
    """Test ExecutionProjectionService."""

    def setUp(self):
        self.service = ExecutionProjectionService()

    def test_build_and_cache(self):
        """测试构建和缓存"""
        task_progress = [
            {"task_id": "t1", "subject": "任务1", "status": "in_progress"},
        ]

        view1 = self.service.build_projection("goal-123", task_progress)
        view2 = self.service.get_cached_projection("goal-123")

        self.assertIsNotNone(view2)
        self.assertEqual(view1.goal_id, view2.goal_id)

    def test_invalidate_cache(self):
        """测试缓存失效"""
        task_progress = [
            {"task_id": "t1", "subject": "任务1", "status": "in_progress"},
        ]

        self.service.build_projection("goal-123", task_progress)
        self.assertIsNotNone(self.service.get_cached_projection("goal-123"))

        self.service.invalidate_cache("goal-123")
        self.assertIsNone(self.service.get_cached_projection("goal-123"))

    def test_build_bulk(self):
        """测试批量构建"""
        goals_with_tasks = [
            {
                "goal_id": "goal-1",
                "task_progress": [
                    {"task_id": "t1", "subject": "任务1", "status": "completed"},
                ],
            },
            {
                "goal_id": "goal-2",
                "task_progress": [
                    {"task_id": "t2", "subject": "任务2", "status": "in_progress"},
                ],
            },
        ]

        results = self.service.build_bulk_projections(goals_with_tasks)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].goal_id, "goal-1")
        self.assertEqual(results[1].goal_id, "goal-2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
