"""尚书令PM系统全方位压测

测试内容：
1. 功能压测：1000需求注册、500任务并发更新、100次文档版本
2. 集成压测：PM-Director 50轮迭代、完整链路验证
3. 压力测试：1000次/秒状态更新、10MB文档解析、10000任务查询
"""

import json
import os
import random
import shutil
import string
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List

# Skip this test - pm.* modules have been migrated to polaris/cells
import pytest
pytest.importorskip("polaris.cells.pm")

# Add paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'backend', 'scripts'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'backend', 'core', 'polaris_loop'))

from pm.state_manager import get_state_manager, PMStateManager
from pm.requirements_tracker import (
    get_requirements_tracker,
    RequirementsTracker,
    RequirementStatus,
    RequirementPriority,
    RequirementType,
)
from pm.document_manager import get_document_manager, DocumentManager
from pm.task_orchestrator import (
    get_task_orchestrator,
    TaskOrchestrator,
    TaskStatus,
    TaskPriority,
    AssigneeType,
    TaskVerification,
)
from pm.execution_tracker import get_execution_tracker, ExecutionTracker
from pm.pm_integration import get_pm, PM


class TestPMBase(unittest.TestCase):
    """Base test class with setup/teardown."""

    def setUp(self):
        """Set up test workspace."""
        self.test_dir = tempfile.mkdtemp(prefix="shangshuling_test_")
        self.workspace = os.path.join(self.test_dir, "workspace")
        os.makedirs(self.workspace)

        # Initialize PM
        self.pm = get_pm(self.workspace)
        self.pm.initialize(project_name="Test Project", description="压测项目")

    def tearDown(self):
        """Clean up test workspace."""
        shutil.rmtree(self.test_dir, ignore_errors=True)


class TestFunctionalStress(TestPMBase):
    """功能压测 - 大规模数据操作."""

    def test_01_register_1000_requirements(self):
        """测试：注册1000个需求."""
        print("\n[压测] 注册1000个需求...")
        start_time = time.time()

        for i in range(1000):
            self.pm.requirements.register_requirement(
                title=f"需求 {i+1}: 测试功能",
                description=f"这是第 {i+1} 个需求的详细描述",
                source="docs/test.md",
                source_section=f"section-{i+1}",
                priority=random.choice(list(RequirementPriority)),
                req_type=random.choice(list(RequirementType)),
                tags=["test", f"batch-{i//100}"],
            )

        elapsed = time.time() - start_time
        print(f"  完成: 1000个需求注册，耗时 {elapsed:.2f}秒")
        print(f"  平均: {elapsed/1000*1000:.2f}ms/需求")

        # Verify
        all_reqs = self.pm.requirements.list_requirements()
        self.assertEqual(len(all_reqs), 1000)

        # Check coverage
        coverage = self.pm.requirements.get_coverage_report()
        self.assertEqual(coverage["total"], 1000)

    def test_02_concurrent_task_updates(self):
        """测试：500个任务并发状态更新."""
        print("\n[压测] 500个任务并发状态更新...")

        # First register 500 tasks
        task_ids = []
        for i in range(500):
            task = self.pm.tasks.register_task(
                title=f"任务 {i+1}",
                description=f"任务 {i+1} 描述",
                priority=random.choice(list(TaskPriority)),
            )
            task_ids.append(task.id)

        print(f"  已注册500个任务")

        # Concurrent updates
        start_time = time.time()

        def update_task(task_id):
            self.pm.tasks.assign_task(
                task_id,
                f"executor-{random.randint(1, 10)}",
                AssigneeType.DIRECTOR,
            )
            return task_id

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(update_task, tid) for tid in task_ids]
            results = [f.result() for f in as_completed(futures)]

        elapsed = time.time() - start_time
        print(f"  完成: 500个任务并发分配，耗时 {elapsed:.2f}秒")
        print(f"  平均: {elapsed/500*1000:.2f}ms/任务")

        # Verify
        assigned = self.pm.tasks.get_tasks_by_status(TaskStatus.ASSIGNED)
        self.assertEqual(len(assigned), 500)

    def test_03_document_version_iterations(self):
        """测试：100次文档版本迭代."""
        print("\n[压测] 100次文档版本迭代...")

        doc_path = os.path.join(self.workspace, "test_doc.md")
        content_base = "# 测试文档\n\n"

        start_time = time.time()

        for i in range(100):
            content = content_base + f"\n## 版本 {i+1}\n\n"
            content += f"这是第 {i+1} 次更新的内容。\n" * 50

            # Write file
            with open(doc_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Create version
            self.pm.documents.create_version(
                doc_path=doc_path,
                content=content,
                change_summary=f"第 {i+1} 次更新",
            )

        elapsed = time.time() - start_time
        print(f"  完成: 100次文档版本迭代，耗时 {elapsed:.2f}秒")
        print(f"  平均: {elapsed/100*1000:.2f}ms/版本")

        # Verify
        versions = self.pm.documents.list_versions(doc_path)
        self.assertEqual(len(versions), 100)


class TestIntegrationStress(TestPMBase):
    """集成压测 - 完整工作流."""

    def test_01_full_requirement_task_code_workflow(self):
        """测试：需求→任务→代码完整链路."""
        print("\n[集成压测] 完整链路验证 (需求→任务→代码)...")

        start_time = time.time()

        # 1. Register requirements
        reqs = []
        for i in range(50):
            req = self.pm.requirements.register_requirement(
                title=f"需求 {i+1}",
                description=f"需求描述 {i+1}",
                source="docs/spec.md",
                priority=RequirementPriority.HIGH,
            )
            reqs.append(req)

        print(f"  步骤1: 注册50个需求 ✓")

        # 2. Create tasks for each requirement
        tasks = []
        for req in reqs:
            for j in range(2):  # 2 tasks per requirement
                task = self.pm.tasks.register_task(
                    title=f"任务: {req.title} - {j+1}",
                    description=f"实现 {req.title}",
                    requirements=[req.id],
                    priority=TaskPriority.HIGH,
                )
                tasks.append(task)

                # Link task to requirement
                self.pm.requirements.link_task(req.id, task.id)

        print(f"  步骤2: 创建100个任务并关联需求 ✓")

        # 3. Assign and complete tasks
        for i, task in enumerate(tasks):
            self.pm.tasks.assign_task(
                task.id,
                f"director-{i % 5 + 1}",
                AssigneeType.DIRECTOR,
            )

            # Complete most tasks
            if i < 90:  # 90% completion rate
                self.pm.tasks.complete_task(
                    task.id,
                    f"director-{i % 5 + 1}",
                    TaskVerification(
                        method="test_passed",
                        evidence=f"test_{task.id}_passed",
                        verified_by=f"director-{i % 5 + 1}",
                    ),
                )

        print(f"  步骤3: 分配任务，完成90% ✓")

        elapsed = time.time() - start_time
        print(f"  完成: 完整链路验证，耗时 {elapsed:.2f}秒")

        # Verify
        coverage = self.pm.requirements.get_coverage_report()
        print(f"  需求覆盖率: {coverage['coverage']:.1f}%")

        task_stats = self.pm.tasks.get_stats_summary()
        print(f"  任务完成率: {task_stats.get('completion_rate', 0) * 100:.1f}%")

        self.assertGreater(coverage["verified"], 40)  # At least 40 verified

    def test_02_pm_director_iterations(self):
        """测试：模拟50轮PM-Director迭代."""
        print("\n[集成压测] 模拟50轮PM-Director迭代...")

        start_time = time.time()
        completed_iterations = 0

        for iteration in range(50):
            # 1. PM: Register tasks
            tasks = []
            for i in range(3):
                task = self.pm.tasks.register_task(
                    title=f"迭代{iteration+1}-任务{i+1}",
                    description=f"第{iteration+1}轮迭代的任务",
                    priority=TaskPriority.MEDIUM,
                )
                tasks.append(task)

            # 2. PM: Assign to Director
            for task in tasks:
                self.pm.tasks.assign_task(
                    task.id,
                    "Director",
                    AssigneeType.DIRECTOR,
                )

            # 3. Director: Execute and report completion
            for task in tasks:
                # Simulate execution
                time.sleep(0.001)

                # Record completion
                self.pm.record_task_completion(
                    task.id,
                    "Director",
                    success=True,
                    result={
                        "verification_method": "test_passed",
                        "evidence": f"iteration_{iteration+1}_passed",
                        "summary": f"Completed in iteration {iteration+1}",
                    },
                )

            completed_iterations += 1

            # Update PM stats
            self.pm.state.increment_stat("total_iterations")

        elapsed = time.time() - start_time
        print(f"  完成: 50轮迭代，耗时 {elapsed:.2f}秒")
        print(f"  平均: {elapsed/50*1000:.2f}ms/轮")

        self.assertEqual(completed_iterations, 50)

        # Verify stats
        pm_state = self.pm.state.get_state()
        self.assertGreaterEqual(pm_state.stats.total_tasks_completed, 150)  # 50*3


class TestPressureStress(TestPMBase):
    """压力测试 - 极端场景."""

    def test_01_high_frequency_state_updates(self):
        """测试：高频状态更新 (1000次/秒)."""
        print("\n[压力测试] 高频状态更新 (1000次)...")

        # Create a task to update
        task = self.pm.tasks.register_task(
            title="高频更新任务",
            description="测试高频状态更新",
        )

        start_time = time.time()

        # Rapid status changes
        for i in range(1000):
            status = random.choice([
                TaskStatus.PENDING,
                TaskStatus.ASSIGNED,
                TaskStatus.IN_PROGRESS,
            ])
            self.pm.tasks.update_task(task.id, status=status)

        elapsed = time.time() - start_time
        rate = 1000 / elapsed

        print(f"  完成: 1000次状态更新，耗时 {elapsed:.2f}秒")
        print(f"  速率: {rate:.2f}次/秒")

        self.assertGreater(rate, 100)  # At least 100 ops/sec

    def test_02_large_document_parsing(self):
        """测试：大文档解析 (10MB)."""
        print("\n[压力测试] 大文档解析 (10MB)...")

        # Generate large document
        doc_path = os.path.join(self.workspace, "large_doc.md")

        # Generate ~10MB content
        lines = []
        for i in range(100000):
            lines.append(f"## Section {i+1}")
            lines.append(f"Content for section {i+1}: " + "x" * 80)
            lines.append("")

        content = "\n".join(lines)
        size_mb = len(content.encode("utf-8")) / (1024 * 1024)

        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(content)

        print(f"  文档大小: {size_mb:.2f}MB")

        start_time = time.time()

        # Analyze document
        analysis = self.pm.documents.analyze_document(doc_path, content)

        elapsed = time.time() - start_time
        print(f"  完成: 文档解析，耗时 {elapsed:.2f}秒")
        print(f"  发现需求: {len(analysis.requirements)}")
        print(f"  发现接口: {len(analysis.interfaces)}")

        # Should complete in reasonable time
        self.assertLess(elapsed, 30)  # Less than 30 seconds

    def test_03_large_scale_task_query(self):
        """测试：大规模任务查询 (10000任务)."""
        print("\n[压力测试] 大规模任务查询 (10000任务)...")

        # Register 10000 tasks
        print("  正在注册10000个任务...")
        task_ids = []
        for i in range(10000):
            task = self.pm.tasks.register_task(
                title=f"批量任务 {i+1}",
                description=f"第 {i+1} 个批量任务",
                priority=random.choice(list(TaskPriority)),
            )
            task_ids.append(task.id)

            if (i + 1) % 1000 == 0:
                print(f"    已注册 {i+1} 个任务")

        # Query test
        print("  执行查询测试...")

        # Test 1: Get all tasks by status
        start_time = time.time()
        pending = self.pm.tasks.get_tasks_by_status(TaskStatus.PENDING)
        elapsed1 = time.time() - start_time

        # Test 2: Get task by ID (random access)
        start_time = time.time()
        sample_ids = random.sample(task_ids, 100)
        for tid in sample_ids:
            self.pm.tasks.get_task(tid)
        elapsed2 = time.time() - start_time

        # Test 3: Topological sort
        start_time = time.time()
        sorted_tasks = self.pm.tasks.topological_sort()
        elapsed3 = time.time() - start_time

        print(f"  查询结果:")
        print(f"    按状态查询10000任务: {elapsed1:.2f}秒")
        print(f"    随机访问100任务: {elapsed2:.2f}秒")
        print(f"    拓扑排序: {elapsed3:.2f}秒")

        self.assertEqual(len(pending), 10000)
        self.assertEqual(len(sorted_tasks), 10000)


class TestSystemIntegrity(TestPMBase):
    """系统完整性测试."""

    def test_01_truth_source_integrity(self):
        """测试：真相源完整性."""
        print("\n[完整性测试] 真相源完整性...")

        # Create task
        task = self.pm.tasks.register_task(
            title="真相测试任务",
            description="测试真相源完整性",
        )

        # Verify initial state
        task_data = self.pm.tasks.get_task(task.id)
        self.assertEqual(task_data.status, TaskStatus.PENDING)

        # Assign
        self.pm.tasks.assign_task(task.id, "Director-1", AssigneeType.DIRECTOR)
        task_data = self.pm.tasks.get_task(task.id)
        self.assertEqual(task_data.status, TaskStatus.ASSIGNED)

        # Complete
        self.pm.tasks.complete_task(
            task.id,
            "Director-1",
            TaskVerification("test_passed", "evidence", "Director-1"),
        )
        task_data = self.pm.tasks.get_task(task.id)
        self.assertEqual(task_data.status, TaskStatus.COMPLETED)

        # Verify verification is recorded
        self.assertIsNotNone(task_data.verification)
        self.assertEqual(task_data.verification["method"], "test_passed")

        print("  真相源状态流转验证通过 ✓")

    def test_02_recovery_after_crash(self):
        """测试：系统崩溃后恢复."""
        print("\n[完整性测试] 崩溃恢复能力...")

        # Create state
        for i in range(100):
            self.pm.tasks.register_task(
                title=f"恢复测试任务 {i+1}",
                description="测试恢复能力",
            )

        # Get state before "crash"
        stats_before = self.pm.tasks.get_stats_summary()

        # Simulate crash by creating new instance (clearing cache)
        from pm.pm_integration import reset_pm
        reset_pm(self.workspace)

        # New instance
        pm2 = get_pm(self.workspace)

        # Verify state is recovered
        stats_after = pm2.tasks.get_stats_summary()
        self.assertEqual(stats_before["total"], stats_after["total"])

        print("  崩溃恢复验证通过 ✓")

    def test_03_concurrent_access_safety(self):
        """测试：并发访问安全性."""
        print("\n[完整性测试] 并发访问安全性...")

        errors = []

        def concurrent_operation(op_id):
            try:
                # Mix of operations
                if op_id % 3 == 0:
                    self.pm.tasks.register_task(
                        title=f"并发任务 {op_id}",
                        description="测试并发安全",
                    )
                elif op_id % 3 == 1:
                    self.pm.requirements.register_requirement(
                        title=f"并发需求 {op_id}",
                        description="测试并发安全",
                        source="test.md",
                    )
                else:
                    self.pm.state.get_state()
                return True
            except Exception as e:
                errors.append(str(e))
                return False

        # Run 100 concurrent operations
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(concurrent_operation, i) for i in range(100)]
            results = [f.result() for f in as_completed(futures)]

        success_count = sum(results)
        print(f"  并发操作: 100次，成功 {success_count}次")

        if errors:
            print(f"  错误: {len(errors)}个")
            for e in errors[:5]:
                print(f"    - {e}")

        self.assertGreater(success_count, 90)  # At least 90% success


def run_all_tests():
    """Run all stress tests."""
    print("=" * 70)
    print("尚书令PM系统全方位压测")
    print("=" * 70)
    print()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestFunctionalStress))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegrationStress))
    suite.addTests(loader.loadTestsFromTestCase(TestPressureStress))
    suite.addTests(loader.loadTestsFromTestCase(TestSystemIntegrity))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print()
    print("=" * 70)
    print("压测总结")
    print("=" * 70)
    print(f"测试总数: {result.testsRun}")
    print(f"成功: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"失败: {len(result.failures)}")
    print(f"错误: {len(result.errors)}")

    if result.wasSuccessful():
        print("\n✓ 所有压测通过！")
        return 0
    else:
        print("\n✗ 部分压测失败")
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
